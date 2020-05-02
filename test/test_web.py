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

import unittest

import sqlalchemy
import sqlalchemy.orm
import cherrypy
from webtest import TestApp

from qaqa_bot import model, game, web
from .util import CONFIG, create_sample_users


class TestWeb(unittest.TestCase):
    def setUp(self) -> None:
        # Setup database schema
        engine = sqlalchemy.create_engine(CONFIG['database']['connection'], echo=False)
        model.Base.metadata.create_all(engine)
        create_sample_users(engine)

        self.game_server = game.GameServer(CONFIG, lambda x: None, engine)

        cherrypy.config.update({'engine.autoreload.on': False})
        cherrypy.server.unsubscribe()
        cherrypy.engine.start()

        self.wsgiapp = cherrypy.tree.mount(web.WebRoot(web.WebEnvironment(CONFIG, self.game_server)))
        self.app = TestApp(self.wsgiapp)

    def tearDown(self) -> None:
        cherrypy.engine.exit()

    def _simple_sample_game(self) -> None:
        self.game_server.new_game(21, "Funny Group")
        self.game_server.join_game(21, 1)
        self.game_server.join_game(21, 2)
        self.game_server.join_game(21, 3)
        self.game_server.set_rounds(21, 2)
        self.game_server.start_game(21)
        self.game_server.submit_text(11, "Question 1")
        self.game_server.submit_text(13, "Question 3")
        self.game_server.submit_text(12, "Question 2")
        self.game_server.join_game(21, 4)
        self.game_server.submit_text(11, "Answer 1")
        self.game_server.submit_text(13, "Answer 3")
        self.game_server.get_group_status(21)
        self.game_server.submit_text(12, "Answer 2")

    def test_simple_result(self) -> None:
        self._simple_sample_game()

        resp = self.app.get("/game/1")
        resp = resp.follow()
        self.assertEqual(200, resp.status_int)
        resp.mustcontain("Question 1")
