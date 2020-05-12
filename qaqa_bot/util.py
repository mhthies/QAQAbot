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
import base64
import binascii
import gettext
import hashlib
import os.path
from contextlib import contextmanager
from typing import Dict, Any, Iterable, List, Union, Optional

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


def encode_secure_id(value: int, secret: str, realm: bytes = b"") -> str:
    """
    Secure a (postive 32-bit) integer id from manipulation/bruteforce testing, by hashing it together with a
    given secret string. The id and the hash are encoded into an url-safe base64 string.

    :param value: The id to be secured
    :param secret: A secret string
    :param realm: A static byte array to be included in the hash, to create different hashes for the same id in
        different realms (e.g. Game id 1 and Sheet id 1).
    :return: The base64-encoded id with hash
    """
    value_bytes = value.to_bytes(4, byteorder='big')
    m = hashlib.sha256()
    m.update(value_bytes)
    m.update(secret.encode('utf-8'))
    m.update(realm)
    digest = m.digest()
    return base64.urlsafe_b64encode(value_bytes + digest).decode('utf-8')


def decode_secure_id(secure_id: str, secret: str, realm: bytes = b"") -> Optional[int]:
    """
    Decode a secured id, generated with `encode_secure_id()` and check the included hash with the given secret.

    :param secure_id: The base64-encoded string from `encode_secure_id()`.
    :param secret: The known secret, that was used to create the secure id
    :param realm: The `realm` the secure_id has been created in
    :return: None if the secure id is invalid (invalid base64, hash does not match, etc.)
    """
    if len(secure_id) < 5:
        return None
    try:
        secure_id_bytes = base64.urlsafe_b64decode(secure_id)
    except binascii.Error:
        return None
    value_bytes = secure_id_bytes[0:4]
    given_digest = secure_id_bytes[4:]
    m = hashlib.sha256()
    m.update(value_bytes)
    m.update(secret.encode('utf-8'))
    m.update(realm)
    check_digest = m.digest()
    if check_digest != given_digest:
        return None
    return int.from_bytes(value_bytes, 'big')


class LazyGetTextBase(metaclass=abc.ABCMeta):
    """
    Abstract base class for the lazy GNU gettext implementation.

    Instances of this class contain a translatable string, that may be translated with a given gettext `Translations`
    environment, as soon as the target locale is known, using `translate_string()`. Additionally, they may contain
    formatting parameters to fill into the translated strings afterwards.
    """
    @abc.abstractmethod
    def get_translation(self, translations: gettext.NullTranslations) -> str:
        pass

    def format(self, **fields) -> "FormattedText":
        return FormattedText(self, fields)

    def join(self, iterable: Iterable[Union[str, "LazyGetTextBase"]]):
        return JoinedText(self, list(iterable))

    def __add__(self, other):
        if isinstance(other, LazyGetTextBase):
            return ConcatGetText(self, other)
        elif isinstance(other, str):
            return ConcatGetText(self, GetNoText(other))
        else:
            return NotImplemented

    def __radd__(self, other):
        if isinstance(other, LazyGetTextBase):
            return ConcatGetText(other, self)
        elif isinstance(other, str):
            return ConcatGetText(GetNoText(str), self)
        else:
            return NotImplemented


class GetText(LazyGetTextBase):
    """ Lazy version of `gettext()`"""
    def __init__(self, message: str):
        self.message = message

    def get_translation(self, translations: gettext.NullTranslations) -> str:
        return translations.gettext(self.message)


class NGetText(LazyGetTextBase):
    """ Lazy version of `ngettext()` """
    def __init__(self, singular: str, plural: str, n: int):
        self.singular = singular
        self.plural = plural
        self.n = n

    def get_translation(self, translations: gettext.NullTranslations) -> str:
        return translations.ngettext(self.singular, self.plural, self.n)


class GetNoText(LazyGetTextBase):
    def __init__(self, message: str):
        self.message = message

    def get_translation(self, translations: gettext.NullTranslations) -> str:
        return self.message


class FormattedText(LazyGetTextBase):
    """ Lazy formatting string.

    This class is the result type of `LazyGetTextBase.format(**kwargs)`. It stores a translatable string and formatting
    parameters. When getting the translation, the formatting parameters are translated recursively and afterwards
    formatted into the translated message, using Python's `str.format()`."""
    def __init__(self, message: LazyGetTextBase, fields: Dict[str, Any]):
        self.message = message
        self.fields = fields

    def get_translation(self, translations: gettext.NullTranslations) -> str:
        translated_fields = {k: (v.get_translation(translations) if isinstance(v, LazyGetTextBase) else v)
                             for k, v in self.fields.items()}
        return self.message.get_translation(translations).format(**translated_fields)


class JoinedText(LazyGetTextBase):
    """ Lazy string joining.

    This class is the result type of `LazyGetTextBase.join(iterator)`. It stores a translatable string and a list of
    parts. When getting the translation, the parts are translated recursively and afterwards joined with the translated
    message using Python's `str.join()`."""
    def __init__(self, message: LazyGetTextBase, parts: List[Union[str, LazyGetTextBase]]):
        self.message = message
        self.parts = parts

    def get_translation(self, translations: gettext.NullTranslations) -> str:
        parts = (p.get_translation(translations) if isinstance(p, LazyGetTextBase) else p
                 for p in self.parts)
        return self.message.get_translation(translations).join(parts)


class ConcatGetText(LazyGetTextBase):
    """ Lazy gettext string concatenating.

    This class is the result type of concatenating lazy gettext objects."""
    def __init__(self, a: LazyGetTextBase, b: LazyGetTextBase):
        self.a = a
        self.b = b

    def get_translation(self, translations: gettext.NullTranslations) -> str:
        return self.a.get_translation(translations) + self.b.get_translation(translations)
