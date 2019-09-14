#!/usr/bin/env python3
#coding: UTF-8

from __future__ import absolute_import, division, print_function

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

OLDDIR = os.getcwd()
os.chdir(dirname(abspath(realpath(__file__))))

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

def load_template(name):
    suffix = '.mustache'
    if not name.endswith(suffix):
        name = '{}{}'.format(name, suffix)
    with open(name, 'r') as fp:
        return fp.read()

def _fix_template(model):
    template = model['tmpls'][0]
    template.update({
        'qfmt': load_template('question'),
        'afmt': load_template('answer'),
    })
    return [template]

def _model_from_db(db):
    model_str = db.execute('SELECT models from col').fetchone()[0]
    models = json.loads(model_str)
    model_id = list(models)[0]
    model = list(models.values())[0]

    loaded_model = genanki.Model(
        int(model_id),
        model['name'],
        fields=model['flds'],
        templates=_fix_template(model),
        css=model['css'],
        # Set type to cloze, this is very important!
        type=1,
    )
    # setattr(loaded_model, '_req', model['req'])
    logging.info('Loaded the model')

    return loaded_model

def _fix_cloze(value):
    pattern = re.compile(r'(\{\{c1::.*?\}\})')
    i = 0
    def repl(s):
        nonlocal i
        i += 1
        return s.group(0).replace('{{c1', '{{c%s' % i)
    new_value = re.sub(pattern, repl, value)
    return i, new_value

TAG_RE = re.compile('<div class="mbooks-noteblock" *>#[^ <>-]+<br/?></div>')
def _maybe_remove_tag(value):
    return re.sub(TAG_RE, '', value)

# def _bold_first_line(value):
#     if '<div class="mbooks-highlightblock"><div class="mbooks-noteblock">意外的成功是变化已经发生的征兆<br>'

def _fix_note_fields(model, note):
    field_names = [x['name'] for x in model.fields]
    fields = list(zip(field_names, note['flds'].split(ANKI_FIELD_SEP)))
    fields_d = dict(fields)
    sort_field = fields_d['Front'] or fields_d['ClozeFront']

    front = remove_tags(fields_d['Front'])
    back = fields_d['Back']
    back_before_br = remove_tags(back.split('<br')[0])
    if front and front == back_before_br:
        back = back.replace(front, '', 1)

    fixed_fields = []
    # Remember how many clozes are there so later we can make up the
    # missing ones.
    n_clozes = 0
    for name, value in fields:
        if name in ('ClozeFront', 'ClozeBack'):
            n_clozes, value = _fix_cloze(value)
        elif name == 'Back':
            # if 'lec02' in value:
            #     import pudb; pudb.set_trace() # yapf: disable
            value = _maybe_remove_tag(back)
        # elif name == 'Front':
        #     value = _bold_first_line(value)
        fixed_fields.append(value)

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

def _fix_cards(db, note_id, note, n_clozes):
    # logging.info('n_clozes = %s', n_clozes)
    if n_clozes <= 1:
        return
    cards = db.execute('SELECT * FROM cards where nid = {}'.format(note_id)).fetchall()
    if not cards or len(cards) > 1:
        return
    fixed_cards = [genanki.Card(card_ord) for card_ord in range(n_clozes)]
    setattr(note, 'cards', fixed_cards)

def _fix_note(db, model, _note):
    note = dict(zip(NOTE_ATTRS, _note))
    n_clozes, sort_field, fields = _fix_note_fields(model, note)
    fixed_note = genanki.Note(
        model=model,
        guid=note['guid'],
        fields=fields,
        sort_field=sort_field,
    )
    _fix_cards(db, note['id'], fixed_note, n_clozes)
    return fixed_note

def _fix_db(db):
    model = _model_from_db(db)
    notes = db.execute('SELECT * FROM notes').fetchall()
    logging.info('Loaded %s notes', len(notes))
    fixed_notes = [_fix_note(db, model, note) for note in notes]
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
@click.argument('path', default='auto')
def fix(path):
    if path == 'auto':
        path = _find_apkg()
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
