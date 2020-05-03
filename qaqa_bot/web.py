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

To simplify setup of the cherrypy engine (including the WebRoot Controller, HTTP server config, no autoreload and custom
error page), the `setup_cherrypy_engine()` function is provided.
"""
import gettext
import os
from typing import Dict, Any, Optional

import cherrypy
import mako.lookup
import markupsafe

from .game import GameServer, LOCALE_DIR
from .util import decode_secure_id, encode_secure_id


def setup_cherrypy_engine(env: "WebEnvironment", config: Dict[str, Any]) -> None:
    """
    Setup the CherryPy global app tree and engine for usage in the Telegram bot.

    This method:
    * disabled CherryPy's autoreload feature
    * Passes all options from the `[web]` config section to CherryPy's global config
    * Mounts an instance of the `WebRoot` Controller class with the given WebEnvironment to  `/`, with static files
      served from `web_static/`.
    * Register a `before_finalize` CherryPy tool to add a Content-Security-Policy header.
    """
    cherrypy.config.update({'engine.autoreload.on': False})
    cherrypy.config.update(config['web'])
    cherrypy.tree.mount(WebRoot(env), '/', {
        '/static': {'tools.staticdir.on': True,
                    'tools.staticdir.dir': os.path.join(os.path.dirname(__file__), 'web_static')},
        '/favicon.ico': {'tools.staticfile.on': True,
                         'tools.staticfile.filename': os.path.join(os.path.dirname(__file__), 'web_static',
                                                                   'favicon.ico')},
    })

    @cherrypy.tools.register('before_finalize', priority=60)
    def secure_headers():
        headers = cherrypy.response.headers
        headers['Content-Security-Policy'] = "default-src 'self';"


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
            'static_url': lambda file_name: "{}/static/{}".format(config['web']['base_url'], file_name),  # TODO add version to control caching
            'encode_id': lambda realm, val: encode_secure_id(val, config['secret'], realm),
        }

    def render_template(self, template_name: str, params: Dict[str, Any], locale: Optional[str] = None) -> str:
        if locale is None:
            translations = gettext.NullTranslations()
        else:
            translations = gettext.translation('qaqa_bot', LOCALE_DIR, (locale,), fallback=True)
        template = self.template_lookup.get_template(template_name)
        return template.render(**self.template_globals, **params,
                               gettext=translations.gettext, ngettext=translations.ngettext)

    @staticmethod
    def _safe(text: str) -> markupsafe.Markup:
        return markupsafe.Markup(text)


class WebRoot:
    def __init__(self, env: WebEnvironment):
        self._env = env
        self.game = Game(env)
        self.sheet = Sheet(env)

    @cherrypy.expose
    def index(self, lang='en'):
        return self._env.render_template('index.mako.html', {}, lang)


@cherrypy.popargs('game_id')
class Game:
    def __init__(self, env: WebEnvironment):
        self._env = env

    @cherrypy.expose
    def index(self, game_id, lang='en'):
        if '/' in lang:
            raise cherrypy.HTTPError(422, "Invalid language code")
        game_id_decoded = decode_secure_id(game_id, self._env.config['secret'], b'game')
        if game_id_decoded is None:
            raise cherrypy.HTTPError(404, "Invalid game id string")
        game = self._env.game_server.get_game_result(game_id_decoded)
        if game is None:
            raise cherrypy.HTTPError(404, "Game with given id not found")
        return self._env.render_template('game_result.mako.html', {'game': game}, lang)


@cherrypy.popargs('sheet_id')
class Sheet:
    def __init__(self, env: WebEnvironment):
        self._env = env

    @cherrypy.expose
    def index(self, sheet_id, lang='en'):
        if '/' in lang:
            raise cherrypy.HTTPError(422, "Invalid language code")
        sheet_id_decoded = decode_secure_id(sheet_id, self._env.config['secret'], b'sheet')
        if sheet_id_decoded is None:
            raise cherrypy.HTTPError(404, "Invalid sheet id string")
        sheet = self._env.game_server.get_game_result_sheet(sheet_id_decoded)
        if sheet is None:
            raise cherrypy.HTTPError(404, "Sheet with given sheet id not found")
        return self._env.render_template('sheet_result.mako.html', {'sheet': sheet}, lang)
