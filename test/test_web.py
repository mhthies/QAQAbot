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
import re
import unittest
from typing import List, Optional

import sqlalchemy
import sqlalchemy.orm
import cherrypy
from webtest import TestApp

from qaqa_bot import model, game, web
from .util import CONFIG, create_sample_users, OutgoingMessageStore


class TestWeb(unittest.TestCase):
    def setUp(self) -> None:
        # Setup database schema
        engine = sqlalchemy.create_engine(CONFIG['database']['connection'], echo=False)
        model.Base.metadata.create_all(engine)
        create_sample_users(engine)

        self.message_store = OutgoingMessageStore()
        self.game_server = game.GameServer(CONFIG, self.message_store.send_message, engine)

        cherrypy.config.update({'engine.autoreload.on': False})
        cherrypy.server.unsubscribe()
        cherrypy.engine.start()

        self.wsgiapp = cherrypy.tree.mount(web.WebRoot(web.WebEnvironment(CONFIG, self.game_server)))
        self.app = TestApp(self.wsgiapp)

    def tearDown(self) -> None:
        cherrypy.engine.exit()

    def _simple_sample_game(self) -> List[game.TranslatedMessage]:
        self.game_server.new_game(21, "Funny Group")
        self.game_server.join_game(21, 1)
        self.game_server.join_game(21, 2)
        self.game_server.join_game(21, 3)
        self.game_server.set_rounds(21, 2)
        self.game_server.start_game(21)
        self.game_server.submit_text(11, 1, "Question 1")
        self.game_server.submit_text(13, 2, "Question 3")
        self.game_server.submit_text(12, 3, "Question 2")
        self.game_server.join_game(21, 4)
        self.game_server.submit_text(11, 4, "Answer 1")
        self.game_server.submit_text(13, 5, "Answer 3")
        self.game_server.get_group_status(21)
        self.message_store.fetch_messages()
        self.game_server.submit_text(12, 6, "Answer 2")
        return self.message_store.fetch_messages()

    @staticmethod
    def _find_result_url(messages: List[game.TranslatedMessage], chat_id: int) -> Optional[str]:
        re_url = re.compile(re.escape(CONFIG['web']['base_url']) + r"(\/[^\s\"]+)")
        for message in messages:
            if message.chat_id == chat_id:
                match = re_url.search(message.text)
                if match:
                    return match[1]
        return None

    def test_simple_result(self) -> None:
        finalize_messages = self._simple_sample_game()
        result_path = self._find_result_url(finalize_messages, 21)
        self.assertIsNotNone(result_path)

        resp = self.app.get(result_path)
        self.assertEqual(200, resp.status_int)
        resp.mustcontain("Question 1")
        resp = resp.click(href=re.compile(r'/sheet/'), index=0)
        self.assertEqual(200, resp.status_int)
        self.assertRegex(resp.text, r".*Question [1-3].*")
