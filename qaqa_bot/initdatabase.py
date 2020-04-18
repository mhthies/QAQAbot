import sqlalchemy.orm
import toml
from qaqa_bot import model


# Setup database schema
CONFIG = toml.load("config.toml")

engine = sqlalchemy.create_engine(CONFIG['database']['connection'], echo=True)
model.Base.metadata.create_all(engine)
