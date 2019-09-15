
import re
import sys
from abc import ABC, abstractmethod

from w3lib.html import remove_tags


class FieldProcessor(ABC):
    @abstractmethod
    def process_note_fields(self, fields):
        pass

class SingleFieldProcess(FieldProcessor):
    def process_note_fields(self, fields):
        processed = []
        for name, value in fields.items():
            processed.append((name, self.process_one_field(name, value)))
        return fields.update(dict(processed))

    @abstractmethod
    def process_one_field(self, field, value):
        pass

TAG_PART = '#[^ <>-]+'
TAG_RE_STRS = [
    r'\<div class="mbooks-noteblock" *\>{}\<br/?\>\</div\>'.format(TAG_PART),
    r'\<br\>{}\<br\>'.format(TAG_PART),
]
TAG_RES = [re.compile(x) for x in TAG_RE_STRS]
def remove_mn_tags(value):
    for r in TAG_RES:
        value = re.sub(r, '', value)
    return value

class TagRemover(SingleFieldProcess):
    def process_one_field(self, name, value):
        if name == 'Back':
            return remove_mn_tags(value)
        return value

class RemoveFrontFromBack(FieldProcessor):
    def process_note_fields(self, fields):
      front = remove_tags(fields['Front'])
      back = fields['Back']
      back_before_br = remove_tags(back.split('<br')[0])
      if front and front == back_before_br:
          back = back.replace(front, '', 1)
      fields['Back'] = back
      return fields

class FixClozeBack(FieldProcessor):
    def process_note_fields(self, fields):
        if fields.get('ClozeFront') and not fields.get('ClozeBack'):
            fields['ClozeBack'] = fields['ClozeFront']

GENIUS_LINK_RE = re.compile(r'\(https://genius\.com.+?\)')

class GeniusLinkRemover(SingleFieldProcess):
    def process_one_field(self, name, value):
        if isinstance(value, str):
            return re.sub(GENIUS_LINK_RE, '', value)
        return value

_FIELD_PROCESSORS = [
    RemoveFrontFromBack(),
    TagRemover(),
    FixClozeBack(),
    GeniusLinkRemover(),
]

def run_fields_processors(fields):
    keys = list(fields)
    for proc in _FIELD_PROCESSORS:
        proc.process_note_fields(fields)
    fields = dict((k, fields[k]) for k in keys)
    return fields

def test_genius_remover():
    orig = '''
There are three things I look for in a hire. Are they smart? Do they get things done? (https://genius.com/4513736/Sam-altman-lecture-2-ideas-products-teams- and-execution-part-ii/Do-they-get-things-done) Do I want to spend a lot of time around them?'''
    expected = '''
There are three things I look for in a hire. Are they smart? Do they get things done?  Do I want to spend a lot of time around them?'''
    assert GeniusLinkRemover().process_one_field('foo', orig) == expected

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        import pytest
        pytest.main([__file__, '-v'] + sys.argv[2:])
