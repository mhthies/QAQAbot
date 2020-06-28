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

import os.path
from typing import List

import toml
import sqlalchemy.orm

from qaqa_bot import game, model
from qaqa_bot.util import session_scope

CONFIG = toml.load(os.path.join(os.path.dirname(__file__), "test_config.toml"))


def create_sample_users(engine):
    users = [model.User(api_id=1, chat_id=11, first_name="Michael"),
             model.User(api_id=2, chat_id=12, first_name="Jenny"),
             model.User(api_id=3, chat_id=13, first_name="Lukas"),
             model.User(api_id=4, chat_id=14, first_name="Jannik")]
    Session = sqlalchemy.orm.sessionmaker(bind=engine)
    with session_scope(Session) as session:
        for user in users:
            session.add(user)
