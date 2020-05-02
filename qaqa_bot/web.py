# Copyright 2020 Michael Thies
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""
A web frontend for the QAQA game bot to serve the game results as HTML pages.

This module defines frontend Controller classes to be used with the CherryPy web engine. The root entry point is an
object of the class `WebRoot`, which can be registered with the CherryPy engine:
`cherrypy.tree.mount(WebRoot(env), '/')`. It provides handler functions for the following endpoints:
* /
* /game/<game_id>/
* /game/<game_id>/sheet/<sheet_id>/

Since I don't like global (or magic thread-local) data, all global (i.e. application-local) data for the frontend
methods (esp. the GameServer object as a backend, the config and the template rendering engine) are encapsulated in an
`WebEnvironment` object and explicitly passed to the Controller's init methods.
"""

import os
from typing import Dict, Any

import cherrypy
import mako.lookup
import markupsafe

from .game import GameServer


class WebEnvironment:
    """ Shared env for all CherryPy controller objects

    I don't like global variables (incl. magic thread-local variables), so we pass this application-local env
    explicitly to the single CherryPy controller objects. It contains the Mako template lookup thing and our application
    backend, the `GameServer`."""

    def __init__(self, config: Dict[str, Any], game_server: GameServer):
        self.game_server = game_server
        self.config = config
        self.template_lookup = mako.lookup.TemplateLookup(
            directories=[os.path.join(os.path.dirname(__file__), 'templates')],
            default_filters=['h'])
        self.template_globals = {
            'safe': self._safe,
            'base_url': config['web']['base_url'],
        }

    def render_template(self, template_name: str, params: Dict[str, Any]) -> str:
        template = self.template_lookup.get_template(template_name)
        return template.render(**self.template_globals, **params)

    @staticmethod
    def _safe(text: str) -> markupsafe.Markup:
        return markupsafe.Markup(text)


class WebRoot:
    def __init__(self, env: WebEnvironment):
        self._env = env
        self.game = Game(env)

    @cherrypy.expose
    def index(self):
        return "hello world"


@cherrypy.popargs('game_id')
class Game:
    def __init__(self, env: WebEnvironment):
        self._env = env
        self.sheet = Sheet(env)

    @cherrypy.expose
    def index(self, game_id):
        game = self._env.game_server.get_game_result(game_id)
        return self._env.render_template('game_result.mako.html', {'game': game})


@cherrypy.popargs('sheet_id')
class Sheet:
    def __init__(self, env: WebEnvironment):
        self._env = env

    @cherrypy.expose
    def index(self, sheet_id):
        return f"Sheet:  {sheet_id}"
