#!/usr/bin/env python3
#coding: UTF-8

import copy
import datetime
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from os.path import (
    abspath,
    basename,
    dirname,
    exists,
    expanduser,
    isabs,
    join,
    realpath,
)

import click
from w3lib.html import remove_tags

import genanki
from processors import run_fields_processors

OLDDIR = os.getcwd()
os.chdir(dirname(abspath(realpath(__file__))))

sys.path.insert(0, '.')

@click.group()
@click.pass_context
def main(ctx):
    ctx.obj['foo'] = 'bar'

def _check_or_mkdir(a, *parts):
    d = join(a, *parts)
    if not exists(d):
        os.mkdir(d)
    return d

WORKDIR = '/tmp/mn-anki-exports-fix'
OUTPUT_FILE = '/tmp/output.apkg'
DBNAME = 'collection.anki2'
ANKI_FIELD_SEP = '\x1f'

def _fix_dbfile(dbfile):
    with sqlite3.connect(dbfile) as db:
        return _fix_db(db)

def load_file(name):
    with open(join('files', name), 'r') as fp:
        return fp.read()

def _fix_cloze_template(model):
    template = copy.deepcopy(model['tmpls'][0])
    template.update({
        'qfmt': load_file('cloze_question.mustache'),
        'afmt': load_file('cloze_answer.mustache'),
    })
    return [template]

def _fix_non_cloze_template(model):
    template = copy.deepcopy(model['tmpls'][0])
    template.update({
        'qfmt': load_file('non_cloze_question.mustache'),
        'afmt': load_file('non_cloze_answer.mustache'),
    })
    return [template]

def _model_from_db(db):
    model_str = db.execute('SELECT models from col').fetchone()[0]
    models = json.loads(model_str)
    model_id = list(models)[0]
    model = list(models.values())[0]

    # Switch first (Front) & second (ClozeFront) field to use the
    # latter as sort field
    cloze_model_fields = copy.deepcopy(model['flds'])
    _swap_first_two(cloze_model_fields)

    css = model['css']
    css += '\n' + load_file('custom.css')

    cloze_model = genanki.Model(
        int(model_id) + 1,
        model['name'] + '_CustomCloze',
        fields=cloze_model_fields,
        templates=_fix_cloze_template(model),
        css=css,
        # Set type to cloze, this is very important!
        type=1,
    )

    non_cloze_model_fields = [x for x in copy.deepcopy(model['flds'])
                          if x['name'] not in NON_CLOZE_EXCLUDED_FIELDS]
    non_cloze_model = genanki.Model(
        int(model_id) + 2,
        model['name'] + '_Custom',
        fields=non_cloze_model_fields,
        templates=_fix_non_cloze_template(model),
        css=css,
        # Set type to non-cloze
        type=0,
    )
    logging.info('Loaded the model')

    return cloze_model, non_cloze_model

def _fix_cloze(value):
    pattern = re.compile(r'(\{\{c1::.*?\}\})')
    i = 0
    def repl(s):
        nonlocal i
        i += 1
        return s.group(0).replace('{{c1', '{{c%s' % i)
    new_value = re.sub(pattern, repl, value)
    return i, new_value

def _swap_first_two(l):
    assert len(l) >= 2
    l[0], l[1] = l[1], l[0]
    return l

def _fix_cloze_note_fields(model, note):
    # The first two fields has been swapped, we need to remap
    field_names = _swap_first_two([x['name'] for x in model.fields])
    fields = list(zip(field_names, note['flds'].split(ANKI_FIELD_SEP)))
    fields_d = dict(fields)
    sort_field = fields_d['ClozeFront']

    fixed_fields = []
    # Remember how many clozes are there so later we can make up the
    # missing ones.
    n_clozes = 0
    for name, value in fields:
        if name == 'ClozeFront':
            n_clozes, value = _fix_cloze(value)
        fixed_fields.append(value)

    processed = run_fields_processors(dict(zip(fields_d, fixed_fields)))
    fixed_fields = list(processed.values())

    _swap_first_two(fixed_fields)

    return n_clozes, sort_field, fixed_fields


def _load_deck(db):
    deck_str = db.execute('SELECT decks from col').fetchone()[0]
    decks = json.loads(deck_str)
    deck = [x for x in decks.values() if x['name'] != 'Default'][0]
    deck_id = deck['id']
    loaded_deck = genanki.Deck(
        deck_id=deck_id,
        name=deck['name']
    )
    return loaded_deck

CARD_ATTRS = [
    'id',
    'nid',
    'did',
    'ord',
    'mod',
    'usn',
    'type',
    'queue',
    'due',
    'ivl',
    'factor',
    'reps',
    'lapses',
    'left',
    'odue',
    'odid',
    'flags',
    'data',
]

NOTE_ATTRS = [
    "id",
    "guid",
    "mid",
    "mod",
    "usn",
    "tags",
    "flds",
    "sfld",
    "csum",
    "flags",
    "data",
]

_card_id = None
def get_card_id():
    global _card_id
    if _card_id is None:
        _card_id = int(time.time() * 1000)
    _card_id += 1
    return _card_id

_note_id = None
def get_note_id():
    global _note_id
    if _note_id is None:
        _note_id = int(time.time())
    _note_id += 1
    return _note_id

def _fix_cloze_cards(db, note_id, note, n_clozes):
    # logging.info('n_clozes = %s', n_clozes)
    if n_clozes == 0:
        return
    cards = db.execute('SELECT * FROM cards where nid = {}'.format(note_id)).fetchall()
    if not cards or len(cards) > 1:
        return
    fixed_cards = [genanki.Card(card_ord, card_id=get_card_id())
                   for card_ord in range(n_clozes)]
    setattr(note, 'cards', fixed_cards)

def is_empty_field(v):
    return not bool(remove_tags(v).strip())

NON_CLOZE_EXCLUDED_FIELDS = (
    'ClozeFront',
    'ClozeBack',
)

def _fix_non_cloze_note_fields(non_cloze_model, fields):
    fixed_fields = dict([
        (name, fields[name]) for name in fields
        if name not in NON_CLOZE_EXCLUDED_FIELDS
    ])
    return list(run_fields_processors(fixed_fields).values())

def _fix_note(db, cloze_model, non_cloze_model, _note):
    # Turn a sql row to a dict
    note = dict(zip(NOTE_ATTRS, _note))

    field_names = _swap_first_two([x['name'] for x in cloze_model.fields])
    fields = dict(list(zip(field_names, note['flds'].split(ANKI_FIELD_SEP))))
    if is_empty_field(fields['ClozeFront']):
        # A non-cloze note
        fixed_fields = _fix_non_cloze_note_fields(non_cloze_model, fields)
        fixed_note = genanki.Note(
            model=non_cloze_model,
            guid=note['guid'],
            fields=fixed_fields,
            note_id=get_note_id(),
        )
    else:
        n_clozes, sort_field, fields = _fix_cloze_note_fields(cloze_model, note)
        fixed_note = genanki.Note(
            model=cloze_model,
            guid=note['guid'],
            fields=fields,
            sort_field=sort_field,
            note_id=note['id'],
        )
        _fix_cloze_cards(db, note['id'], fixed_note, n_clozes)
    return fixed_note

def _fix_db(db):
    cloze_model, non_cloze_model = _model_from_db(db)
    notes = db.execute('SELECT * FROM notes').fetchall()
    logging.info('Loaded %s notes', len(notes))
    fixed_notes = [_fix_note(db, cloze_model, non_cloze_model, note) for note in notes]
    logging.info('Fixed all %s notes', len(notes))

    deck = _load_deck(db)
    logging.info('Loaded deck info: deck name = %s, id = %s', deck.deck_id, deck.name)
    for note in fixed_notes:
        deck.add_note(note)

    logging.info('Generating output file %s', OUTPUT_FILE)
    genanki.Package(deck).write_to_file(OUTPUT_FILE)

def _find_apkg():
    path = subprocess.getoutput('ls -1t ~/Downloads/*.apkg|head -1').strip()
    logging.info('Auto located apkg file %s', path)
    return path

@main.command()
def model():
    """
    This command uses a demo apkg. It's purpose is just to regenerate
    the models to be imported into anki.
    """
    _fix_path('files/testdeck.apkg')

@main.command()
@click.argument('path', default='auto')
def fix(path):
    if path == 'auto':
        path = _find_apkg()
    _fix_path(path)

def _fix_path(path):
    _check_or_mkdir(WORKDIR)
    path = expanduser(path)
    if not exists(path):
        raise RuntimeError('{} doesn not exist!'.format(path))
    with zipfile.ZipFile(path) as zfp:
        logging.info('files: %s', zfp.namelist())
        with tempfile.TemporaryDirectory(dir=WORKDIR) as tempdir:
            zfp.extract(DBNAME, path=tempdir)
            logging.info('Extracted %s to %s', DBNAME, tempdir)
            dbfile = join(tempdir, DBNAME)
            _fix_dbfile(dbfile)

def test_fix_cloze():
    maps = {
        '{{c1::hello}}': '{{c1::hello}}',
        '{{c1::hello}} {{c1::world}}': '{{c1::hello}} {{c2::world}}',
        '{{c1::hello}} my {{c1::world}}': '{{c1::hello}} my {{c2::world}}',
        '{{c1::hello}} {{c1::world}} {{c1::hey}}': '{{c1::hello}} {{c2::world}} {{c3::hey}}',
    }
    for before, after in maps.items():
        assert _fix_cloze(before) == after

def setup_logging(level=logging.INFO):
    kw = {
        'format': '[%(asctime)s][%(module)s]: %(message)s',
        'datefmt': '%m/%d/%Y %H:%M:%S',
        'level': level,
        'stream': sys.stdout
    }

    logging.basicConfig(**kw)

if __name__ == '__main__':
    setup_logging()
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        import pytest
        pytest.main([__file__, '-v'] + sys.argv[2:])
    else:

        main(obj={})
