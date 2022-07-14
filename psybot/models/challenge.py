from mongoengine import Document, StringField, IntField, BooleanField, ListField, ReferenceField, LongField, MapField, \
    EmbeddedDocumentListField, EmbeddedDocument
from psybot.models.ctf import Ctf


class Working(EmbeddedDocument):
    user = LongField(required=True, unique=True)
    value = IntField(required=True)


class Challenge(Document):
    name = StringField(required=True)
    channel_id = LongField(required=True)
    category = StringField(required=True)
    ctf = ReferenceField(Ctf, required=True)
    solvers = ListField(LongField(), default=[])
    working = EmbeddedDocumentListField(Working)
    solved = BooleanField(required=True, default=False)