import itertools
from contextlib import contextmanager
from typing import Type, TypeVar, Iterable, Tuple

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


T = TypeVar('T')


def pairwise(iterable: Iterable[T]) -> Iterable[Tuple[T, T]]:
    """s -> (s0,s1), (s1,s2), (s2, s3), ...
    From https://docs.python.org/3/library/itertools.html"""
    a, b = itertools.tee(iterable)
    next(b, None)
    return zip(a, b)