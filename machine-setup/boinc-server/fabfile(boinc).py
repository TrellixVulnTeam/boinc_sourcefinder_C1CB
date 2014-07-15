"""fabric file for the boinc server that is connected to the nfs-server as a backup"""

import os 
import sys
import boto
import glob
import fabric

from fabric import fabric.*



AMI_INSTANCE = 
YUM_PACKAGES = 'autoconf automake binutils gcc gcc-c++ libpng-devel libstdc++46-static gdb libtool gcc-gfortran git openssl-devel mysql mysql-devel python-devel python27 python27-devel '
BOINC_PACKAGES = 'httpd httpd-devel mysql-server php php-cli php-gd php-mysql mod_fcgid php-fpm postfix ca-certificates MySQL-python'
PIP_PACKAGES = 'boto sqlalchemy mysql'
AWS_KEYS = 
PUBLIC_DNS = 'ec2-user@.........compute-1.amazonaws.com'

def nfs_connect(shared_directory):
    """connect the nfs server to the /projects directory of the BOINC server"""
    sudo('mount -t nfs {0}:/{1} /projects'.format(PUBLIC_DNS, shared_directory))
    
    

def general_install():
    yum_update()
    
    sudo('pip install {0}'.format(PIP_PACKAGES))
    sudo('yum install {0}'.format(YUM_PACKAGES))
    #setup pythonpath
    append('/home/ec2-user/.bach_profile',
           ['',
            'PYTHONPATH=/home/ec2-user/boinc/py:/home/ec2-user/boinc-sourcefinder/server/src',
            'export PYTHONPATH'])
    
    
    
def boinc_install():
    yum_update()
    general_install()
    
    sudo('yum install {0}'.format(BOINC_PACKAGES))
    
    sudo('git clone git://boinc.berkeley.edu/boinc-v2.git boinc')
    
    with cd('/home/ec2-users/')
    
    
    
def yum_update():
    """Update general machine packages"""
    sudo('yum install update')


#Kevin's code
def create_instance(ebs_size, ami_name):
    """
    Create the AWS instance
    :param ebs_size:   
    """
    
    ec2_connection = boto.connect_ec2()
    
    dev_sda = blockdevicemapping.EBSBlockDeviceType(delete_on_termination=True)
    dev_sda = int(ebs_size)
    bdm['/dev/sda'] = dev_sda
     reservations = ec2_connection.run_instances(AMI_ID, instance_type=INSTANCE_TYPE, key_name=KEY_NAME, security_groups=SECURITY_GROUPS, block_device_map=bdm)
    instance = reservations.instances[0]
    # Sleep so Amazon recognizes the new instance
    for i in range(4):
        fastprint('.')
        time.sleep(5)

    # Are we running yet?
    while not instance.update() == 'running':
        fastprint('.')
        time.sleep(5)

    # Sleep a bit more Amazon recognizes the new instance
    for i in range(4):
        fastprint('.')
        time.sleep(5)
    puts('.')

    ec2_connection.create_tags([instance.id], {'Name': '{0}'.format(ami_name)})

    # The instance is started, but not useable (yet)
    puts('Started the instance now waiting for the SSH daemon to start.')
    for i in range(12):
        fastprint('.')
        time.sleep(5)
    puts('.')

    # Return the instance
    return instance, ec2_connection
