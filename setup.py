import io
import os
import re

from glob import glob
from os.path import basename
from os.path import splitext
from setuptools import find_packages
from setuptools import setup


def read(filename):
    filename = os.path.join(os.path.dirname(__file__), filename)
    text_type = type(u"")
    with io.open(filename, mode="r", encoding='utf-8') as fd:
        return re.sub(text_type(r':[a-z]+:`~?(.*?)`'), text_type(r'``\1``'), fd.read())


reqs_path = os.path.join(os.path.dirname(__file__), 'requirements.txt')

with open(reqs_path, 'r') as req_file:
    dependencies = req_file.readlines()


setup(
    name="aws_marketplace_ubuntu_scraper",
    version="0.0.4",
    url="https://github.com/philroche/aws-marketplace-ubuntu-scraper",
    license='GPLv3',
    author="Philip Roche",
    author_email="phil.roche@canonical.com",
    description="CLI to return the Ubuntu AMIs in AWS marketplace",
    long_description=read("README.md"),
    long_description_content_type="text/markdown",
    packages=find_packages(exclude=('tests',)),
    py_modules=[splitext(basename(path))[0] for path in glob('*.py')],
    install_requires=dependencies,
    setup_requires=['wheel'],
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
    ],
    entry_points={
        'console_scripts': [
            'aws_marketplace_ubuntu_scraper = '
            'aws_marketplace_ubuntu_scraper:main',
        ],
    },
)
