#! /usr/bin/env python

import os
import sys

# Setup the Python Path as we may be running this via ssh
base_path = os.path.dirname(__file__)
sys.path.append(os.path.abspath(os.path.join(base_path, '/home/ec2-user/projects/duchamp/py')))
sys.path.append('/home/ec2-user/boinc_sourcefinder/server')
sys.path.append('/home/ec2-user/boinc_sourcefinder/server/assimilator')

from utils.logging_helper import config_logger
from utils.amazon_helper import S3Helper, get_file_upload_key
from config import DB_LOGIN, S3_BUCKET_NAME, filesystem
from sqlalchemy import create_engine, select, and_
from sqlalchemy.exc import OperationalError
from database.database_support import CUBE, RESULT
import assimilator
import gzip as gz
import tarfile as tf
import csv
import hashlib
import shutil
from utils.utilities import retry_on_exception, make_path
from Boinc import database, boinc_db
import re

LOG = config_logger(__name__)
LOG.info('PYTHONPATH = {0}'.format(sys.path))

# This represents the valid first row of the csv.
csv_valid_header = ['ParameterNumber','RA','DEC','freq','w_50','w_20','w_FREQ','F_int','F_tot','F_peak','Nvoxel','Nchan','Nspatpix']


class SourcefinderAssimilator(assimilator.Assimilator):

    def __init__(self):
        assimilator.Assimilator.__init__(self)
        self.connection = None
        self.engine = None

    def hash_filecheck(self, file, hashfile):
        with open(file, 'r') as f:
            m = hashlib.md5()
            m.update(f.read())
            hash = m.digest()

        with open(hashfile, 'r') as f:
            hash_from_file = f.read()

        self.logNormal('Hash comparison {0}\n'.format(hash == hash_from_file))

        return hash == hash_from_file

    def get_flat_file_path(self, directory, name):

        path = os.path.join(directory, name)

        if os.path.isfile(path):
            return path

        path += '.tar.gz'

        if os.path.isfile(path):
            return path

        return None

    def run_flat_files(self, directory):
        """
        Used to test the assimilator on a file, rather than running it via the assimilate handler
        :param filename:
        :return:
        """
        database.connect()
        self.engine = create_engine(DB_LOGIN)
        self.connection = self.engine.connect()

        # Get all cube names from the DB.
        # For each cube name, get the canonical result from the db and the name of the canonical result path
        units = database.Workunits.find(assimilate_state=boinc_db.ASSIMILATE_DONE)

        self.logCritical("Starting flat files for wus %d\n", len(units))
        for wu in units:
            self.logCritical('Starting assimilate handler for work unit: {0}\n'.format(wu.name))

            results = database.Results.find(workunit=wu)
            canonical_result = None

            for result in results:
                if result == wu.canonical_result:
                    canonical_result = result
                    break

            if canonical_result is None:
                self.logCritical("No canonical result for %s\n", wu.name)
                continue

            name = re.search('<file_name>(.*)</file_name>', canonical_result.xml_doc_in).group(1)

            path = self.get_flat_file_path(directory, name)

            self.logCritical("Path: %s\n", path)

            if path is None:
                self.logCritical("Canonical result %s doesn't exist in path %s\n", name, directory)
                continue

            # Now assimilate the canonical result
            if self.process_result(wu, path) != 0:
                break

        self.connection.close()
        database.close()

    def get_wu_files(self, wu):
        files = []
        path = filesystem['download']
        wu_name = wu.name + '.fits.gz'

        s = hashlib.md5(wu_name).hexdigest()[:8]
        x = long(s, 16)

        hash_dir_name = "{0}/{1}".format(path, x % int(self.config.uldl_dir_fanout))
        wu_path = os.path.join(hash_dir_name, wu_name)
        wu_path_md5 = wu_path + '.md5'

        self.logNormal("Wu file?: {0}\n".format(wu_path))
        if os.path.exists(wu_path):
            files.append(wu_path)

        self.logNormal("Wu file md5?: {0}\n".format(wu_path_md5))
        if os.path.exists(wu_path_md5):
            files.append(wu_path_md5)

        return files

    def erase_files(self, files):
        deletion_path = '/home/ec2-user/files_to_delete'
        make_path(deletion_path)

        for f in files:
            self.logNormal("Erasing {0}\n".format(f))
            try:
                shutil.move(f, deletion_path)
            except IOError as e:
                self.logCritical("Could not move: {0}\n".format(e.message))

    def assimilate_handler(self, wu, results, canonical_result):
        self.engine = create_engine(DB_LOGIN)
        self.connection = self.engine.connect()

        self.logNormal('Starting assimilate handler for work unit: {0}\n'.format(wu.id))

        if not wu.canonical_result:
            self.logDebug('No canonical result for wu: {0}\n'.format(wu.id))
            return 0

        out_file = self.get_file_path(canonical_result)

        if os.path.isfile(out_file):
            self.logNormal('WU file at {0}\n'.format(out_file))
        else:
            self.logCritical('WU file doesnt exist\n')
            return 0

        retval = self.process_result(wu, out_file)

        if retval == 0:
            # Successful assimilation, erase the work unit file and all the other result files
            files = [self.get_file_path(r) for r in results]
            wu_files = self.get_wu_files(wu)

            self.logNormal("Result files to erase: {0}\n".format(len(files)))
            for f in files:
                self.logNormal("{0}\n".format(f))

            self.logNormal("WU files to erase: {0}\n".format(len(wu_files)))
            for f in wu_files:
                self.logNormal("{0}\n".format(f))

            self.erase_files(files)
            self.erase_files(wu_files)

        self.connection.close()

        return retval

    def process_result(self, wu, file):

        outputs = ''

        self.logCritical("Running on %s\n", file)

        try:
            # The file is a .tar.gz file, but it has no extention when the boinc client returns it
            if not file.endswith(".tar.gz"):
                shutil.copy(file, file + ".tar.gz")
                file += ".tar.gz"

            path = os.path.dirname(file)
            self.logCritical("File Path: %s\n", path)
            # File exists, good to start handling it.

            self.logCritical("Decompressing tar file...\n")

            outputs = os.path.join(path, "outputs")  # this will be the folder that the data is decompressed in to

            self.logCritical("Outputs: %s\n", outputs)

            # It's tar'd
            tar = tf.open(file)
            tar.extractall(path)
            tar.close()

            os.remove(file)

            fs = os.listdir(outputs)
            file_to_use = None
            hashfile = None

            for f in fs:
                if f.endswith('.csv'):
                    file_to_use = f
                    file_to_use = os.path.join(outputs, file_to_use)
                if f.lower().endswith('.md5'):
                    hashfile = f
                    hashfile = os.path.join(outputs, hashfile)

            if file_to_use is None:
                self.logCritical('Client uploaded a WU file, but it does not contain the required CSV file. Cannot assimilate.\n')
                self.logCritical('The following files were included: \n')
                for f in fs:
                    self.logCritical('{0}\n'.format(f))

                return 0

            if hashfile is None:
                self.logCritical("Wu is missing hash file\n")
            else:
                # Confirm the CSV MD5 here
                if not self.hash_filecheck(file_to_use, hashfile):
                    self.logCritical('Hash file check failed on work unit {0}\n'.format(wu.id))
                    self.logCritical('Continuing anyway...\n')
                    # exit? I'm not sure.

            # The CSV is there, final check is that it contains the correct header (first row) that we want

            with open(file_to_use) as f:
                csv_reader = csv.DictReader(f)
                headers = csv_reader.fieldnames

                for i in range(0, len(headers)):
                    if headers[i].strip() != csv_valid_header[i]:
                        self.logCritical('Received CSV is in the wrong format. Field {0}: {1} does not match {2}\n'.format(i, headers[i], csv_valid_header[i]))
                        return 0

                # CSV is good from here

                # These stay constant for all of the results:
                # Run ID (Can be obtained from workunit name)
                # Cube ID (Can be obtained from Run ID and workunit name)

                # These change for each result:
                # Parameter ID (Can be obtained from Run ID and first column in CSV)
                # Each of the other rows in the CSV

                # Example WU name: 6_askap_cube_1_1_19

                underscore = wu.name.find('_')

                try:
                    run_id = int(wu.name[0:underscore])
                except ValueError:
                    self.logCritical('Malformed WU name {0}\n'.format(wu.name))
                    return 0

                cube_name = wu.name[underscore + 1:]

                # First column is the cube ID
                cube_id = retry_on_exception(lambda: (
                    self.connection.execute(select([CUBE]).where(and_(CUBE.c.cube_name == cube_name, CUBE.c.run_id == run_id))).first()[0]), OperationalError,
                                             1)

                # Row 1 is header
                rowcount = 1
                for row in csv_reader:
                    rowcount += 1
                    try:
                        transaction = self.connection.begin()
                        self.connection.execute(
                                RESULT.insert(),
                                cube_id=cube_id,
                                parameter_id=int(row['ParameterNumber']),
                                run_id=run_id,
                                RA=row['RA'],
                                DEC=row['DEC'],
                                freq=row['freq'],
                                w_50=row['w_50'],
                                w_20=row['w_20'],
                                w_FREQ=row['w_FREQ'],
                                F_int=row['F_int'],
                                F_tot=row['F_tot'],
                                F_peak=row['F_peak'],
                                Nvoxel=row['Nvoxel'],
                                Nchan=row['Nchan'],
                                Nspatpix=row['Nspatpix'],
                                workunit_name=wu.name       # Reference in to the boinc DB and in to the s3 file system.
                        )
                        transaction.commit()
                    except ValueError:
                        self.logCritical('Malformed CSV. Parameter number for row {0} is invalid\n'.format(rowcount))
                    except csv.Error as e:
                        self.logCritical('Malformed CSV. Error on line {0}: {1}\n'.format(csv_reader.line_num, e))
                    except:
                        self.logCritical('Undefined error occurred while attempting to load CSV.\n')
                        return 1  # try again later

                self.logNormal('Successfully loaded work unit {0} in to the database\n'.format(wu.name))

                # Update the cube table to reflect this completion
                # Retry this on failure.

                retry_on_exception(lambda: (self.connection.execute(CUBE.update().where(CUBE.c.cube_id == cube_id).values(progress=2)))
                                   , OperationalError, 1) # Retry this function once if it fails the first time.

            # Here is where we copy the data in to an S3 bucket

            if rowcount > 1:  # Only save the file if there's actually results in it.
                for f in fs:
                    s3 = S3Helper(S3_BUCKET_NAME)
                    s3.file_upload(os.path.join(outputs, f), get_file_upload_key(wu.name, f))

        except Exception as e:
            self.logCritical("Error processing work unit: {0}\n".format(e.message))
            return 1  # try again later
        finally:
            if outputs != '':
                shutil.rmtree(outputs)

        return 0


# --------------------------------------------
# Add the following to your assimilator file:

if __name__ == '__main__':

    asm = SourcefinderAssimilator()
    asm.run()

