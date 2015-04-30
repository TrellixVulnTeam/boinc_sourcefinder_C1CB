"""Generates the parameters set for as specified, given the user specified arguments"""

import argparse
import os
from astropy.io import fits
from sqlalchemy import select


def get_cube_names(cube_directory):
    """Return a list of the cubes that need registering for the current run
    :param cube_directory:
    :return:
    """
    directory = os.listdir('{0}.'.format(cube_directory))  # list of cubes in the current run
    return directory


def get_cube_data(cube_file):
    """Retrieve ra,dec,freqencey data from the .fits file
    :param cube_file:
    :return
    """
    hdulist = fits.open(cube_file)
    data_list = ()
    data_list.append(hdulist[0].header[1])  # this will line up with what bits of data within the file contain the ra, dec, freq
    data_list.append(hdulist[0].header[2])
    data_list.append(hdulist[0].header[3])

    return data_list


def update_cube_table(connection, cube_file):
    """Update the database for each cube
    :param cube_file
    """
    data = get_cube_data(cube_file)

