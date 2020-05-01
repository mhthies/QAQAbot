import os

import cherrypy
import mako.lookup

from .game import  GameServer


class WebData:
    """ Shared data for all CherryPy controller objects

    I don't like global variables (incl. magic thread-local variables), so we pass this application-local data
    explicitly to the single CherryPy controller objects. It contains the Mako template lookup thing and our application
    backend, the `GameServer`."""

    def __init__(self, game_server: GameServer):
        self.template_lookup = mako.lookup.TemplateLookup(
            directories=[os.path.join(os.path.dirname(__file__), 'templates')])
        self.game_server = game_server


class WebRoot:
    def __init__(self, data: WebData):
        self._data = data
        self.game = Game(data)

    @cherrypy.expose
    def index(self):
        return "hello world"


@cherrypy.popargs('game_id')
class Game:
    def __init__(self, data: WebData):
        self._data = data
        self.sheet = Sheet(data)

    @cherrypy.expose
    def index(self, game_id):
        template = self._data.template_lookup.get_template('game_result.mako.html')
        return template.render(game_id=game_id, game_result=self._data.game_server.get_game_result(game_id))


@cherrypy.popargs('sheet_id')
class Sheet:
    def __init__(self, data: WebData):
        self._data = data

    @cherrypy.expose
    def index(self, sheet_id):
        return f"Sheet:  {sheet_id}"
