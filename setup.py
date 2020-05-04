#!/usr/bin/env python
import os
from setuptools import setup
from distutils.command.build import build
from distutils.cmd import Command
from pathlib import Path

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po


class BuildLocalization(Command):
    description = "build the po files into mo files"
    def run(self):
        # The working directory is the directory of setup.py.
        base_path = "qaqa_bot/i18n"
        #for filename in os.listdir(base_path):
        for path in Path('qaqa_bot', 'i18n').rglob('*/LC_MESSAGES/*.po'):
            pofile = str(path)
            mofile = str(path.parent / path.stem) + '.mo'
            self.make_file(pofile, mofile, self._build, (pofile, mofile))

    def _build(self, pofile_path, mofile_path):
        with open(pofile_path, "rb") as pofile:
            catalog = read_po(pofile)
        self.mkpath(os.path.dirname(mofile_path))
        with open(mofile_path, "wb") as mofile:
            write_mo(mofile, catalog)

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass


class build_with_l10n(build):
    sub_commands = build.sub_commands + [("build_l10n", None)]


setup(
    name='QAQABot',
    version='1.0',
    description='The Telegram QAQA game bot',
    author='Michael Thies, Jennifer Krieger',
    author_email='mail@mhthies.de, mail@jenniferkrieger.de',
    url='https://gitea.nephos.link/michael/QAQABot',
    packages=['qaqa_bot'],
    package_data={'qaqa_bot': ['i18n/*/LC_MESSAGES/*.mo',
                               'database_versions/*.py',
                               'templates/*',
                               'web_static/*']},
    cmdclass={
        "build_l10n": BuildLocalization,
        "build": build_with_l10n
    },
    python_requires='~=3.6',
    install_requires=[
        'sqlalchemy>=1.3',
        'python-telegram-bot>=12.2',
        'toml>=0.10',
        'alembic>=1.0',
        'cherrypy',
    ],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'License :: OSI Approved :: Apache Software License',
        'Topic :: Communications :: Chat',
        'Topic :: Games/Entertainment',
    ],
)
