# Copyright 2020 Michael Thies, Jennifer Krieger
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.
import logging

import cherrypy
import toml

from .bot import Frontend
from .game import GameServer
from .web import WebRoot, WebEnvironment
from .util import run_migrations
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', '-c', type=argparse.FileType('r'), default='config.toml',
                        help="Configuration TOML file. Defaults to 'config.toml'")
    parser.add_argument('--no-init', action='store_const', const=True, default=False,
                        help="Don't migrate database and set Telegram Bot settings on start")
    parser.add_argument('--init-only', '-i', action='store_const', const=True, default=False,
                        help="Only run database migrations and set Telegram Bot settings, than exit.")
    parser.add_argument('--verbose', '-v', action='count', default=0,
                        help="Make log output more verbose, i.e. reduce log level.")
    parser.add_argument('--quiet', '-q', action='count', default=0,
                        help="Make log output less verbose, i.e. increase log level.")
    args = parser.parse_args()

    logging.basicConfig(level=30 - 10 * args.verbose + 10 * args.quiet)
    config = toml.load(args.config)
    frontend = Frontend(config)
    web_data = WebEnvironment(config, frontend.gs)

    if not args.no_init:
        run_migrations(frontend.gs.database_engine)  # TODO make this better in terms of sensible architecture
        frontend.set_commands()

    if not args.init_only:
        cherrypy.tree.mount(WebRoot(web_data), '/')
        cherrypy.engine.start()
        frontend.start_bot()


if __name__ == '__main__':
    main()
