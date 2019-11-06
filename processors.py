
import re
import sys
from abc import ABC, abstractmethod

from strsimpy.jaccard import Jaccard
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
    r'(\<br/?\>)?{}\<br/?\>'.format(TAG_PART),
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

SIM_THRESHOLD = 0.95
def very_similar(s1, s2):
    if s1 == s2:
        return True
    jaccard = Jaccard(1)
    return jaccard.similarity(s1, s2) >= SIM_THRESHOLD

def unquote_clozes(s):
    def repl(m):
        return m.group(1)
    return re.sub(r'\{\{c[1-9][0-9]*::([^}]+)\}\}', repl, s)

class RemoveFrontFromBack(FieldProcessor):
    field_name = 'Front'

    def process_note_fields(self, fields):
        front = remove_tags(fields[self.field_name])
        if not front:
            return fields
        back = fields['Back']

        f = unquote_clozes(remove_tags(front))
        b = remove_tags(back)
        if not f or not b:
            return fields

        if very_similar(f, b):
            back = ''
        elif '<br' in back:
            parts = back.split('<br', 1)
            back_before_br, remaining = parts
            back_before_br = remove_tags(back_before_br)
            if front != back_before_br:
                parts = back.rsplit('<br', 1)
                back_before_br, remaining = parts
                back_before_br = remove_tags(back_before_br)

            if front == back_before_br:
                back = remaining.lstrip('/>').lstrip('>')

        fields['Back'] = back
        return fields


class RemoveClozeFrontFromBack(RemoveFrontFromBack):
    field_name = 'ClozeFront'


class FixClozeBack(FieldProcessor):
    def process_note_fields(self, fields):
        # We don't use ClozeBack.
        if fields.get('ClozeBack'):
            fields['ClozeBack'] = ''

GENIUS_LINK_RE = re.compile(r'\(https://genius\.com.+?\)')

class GeniusLinkRemover(SingleFieldProcess):
    def process_one_field(self, _, value):
        if isinstance(value, str):
            return re.sub(GENIUS_LINK_RE, '', value)
        return value

BOLD_RE = re.compile(r'\<strong\>(\{\{c[0-9]+:[^}]+\}\})\</strong\>')
class RemoveBoldCloze(SingleFieldProcess):
    def process_one_field(self, name, value):
        if name != 'ClozeFront':
            return value

        def repl(m):
            return m.group(1)

        return re.sub(BOLD_RE, repl, value)

_FIELD_PROCESSORS = [
    GeniusLinkRemover(),
    TagRemover(),
    RemoveFrontFromBack(),
    RemoveClozeFrontFromBack(),
    RemoveBoldCloze(),
    FixClozeBack(),
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
