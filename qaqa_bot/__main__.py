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
import toml

from .bot import Frontend
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
    args = parser.parse_args()

    config = toml.load(args.config)
    frontend = Frontend(config)

    if not args.no_init:
        run_migrations(frontend.gs.database_engine)  # TODO make this better in terms of sensible architecture
        frontend.set_commands()

    if not args.init_only:
        frontend.start_bot()


if __name__ == '__main__':
    main()
