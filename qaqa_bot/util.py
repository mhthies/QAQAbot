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
import abc
import gettext
import os.path
from contextlib import contextmanager
from typing import Dict, Any

import alembic
import alembic.config
import alembic.script
import sqlalchemy.orm


@contextmanager
def session_scope(Session: sqlalchemy.orm.sessionmaker):
    """Provide a transactional scope around a series of operations.
    From https://docs.sqlalchemy.org/en/13/orm/session_basics.html"""
    session: sqlalchemy.orm.Session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_migrations(engine: sqlalchemy.engine.Engine):
    """
    Programmatically run `alembic upgrade head`.

    This method creates a simple alembic Configuration, pointing to our versions directory in ../alembic/versions,
    creates the alembic environment context objects, configures it the
    :class:`~alembic.runtime.migration.MigrationContext` to run migrations for the upgrade function.
    """
    # Create alembic Config
    config = alembic.config.Config()
    # `script_location` is not really required for our use case, but alembic refuses to construct the `ScriptDirectory`
    # without it.
    config.set_main_option('script_location', os.path.join(os.path.dirname(__file__)))
    # multiple locations comma-separated
    config.set_main_option('version_locations', os.path.join(os.path.dirname(__file__), 'database_versions'))
    # Create ScriptDirectory and EnvironmentContext objects
    script_directory = alembic.script.ScriptDirectory.from_config(config)
    context = alembic.environment.EnvironmentContext(config, script_directory)

    # Function to run migrations for (upgrade to head)
    def do_upgrade(revision, _context):
        return script_directory._upgrade_revs('head', revision)

    # Run migrations
    with engine.connect() as connection:
        context.configure(
            connection=connection, fn=do_upgrade
        )

        with context.begin_transaction():
            context.run_migrations()


class LazyGetTextBase(metaclass=abc.ABCMeta):
    """
    Abstract base class for the lazy GNU gettext implementation.

    Instances of this class contain a translatable string, that may be translated with a given gettext `Translations`
    environment, as soon as the target locale is known, using `get_translation()`. Additionally, they may contain
    formatting parameters to fill into the translated strings afterwards.
    """
    @abc.abstractmethod
    def get_translation(self, translations: gettext.NullTranslations) -> str:
        pass


class GetText(LazyGetTextBase):
    """ Lazy version of `gettext()`

    Supports lazy formatting parameters which are applied to the translated string using Python's `str.format()` method.
    """
    def __init__(self, message: str):
        self.message = message
        self.fields: Dict[str, Any] = {}

    def format(self, **fields) -> "GetText":
        self.fields.update(fields)
        return self

    def get_translation(self, translations: gettext.NullTranslations) -> str:
        translated_fields = {k: (v.get_translation if isinstance(v, LazyGetTextBase) else v)
                             for k, v in self.fields.items()}
        return translations.gettext(self.message).format(**translated_fields)


class NGetText(LazyGetTextBase):
    """ Lazy version of `ngettext()` """
    def __init__(self, singular: str, plural: str, n: int):
        self.singular = singular
        self.plural = plural
        self.n = n

    def get_translation(self, translations: gettext.NullTranslations) -> str:
        return translations.ngettext(self.singular, self.plural, self.n)
