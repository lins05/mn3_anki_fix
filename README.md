## What is this

Fix the anki cards exported by MarginNote3 (up to 3.4.3). The problems I suffer are:
- Only one anki card is generated, even if there are multiple clozes.
- Inconsistent & confusing format of the card front / back for cards w/ clozes in the title and cards w/o.

This project reads the database of generated apkg file, and makes use of [genanki](https://github.com/kerrickstaley/genanki) package to rewrite it to a better one.

## How to use

1. Make sure python3 is installed on your Mac.
2. Install the requirements
```sh
    pip3 install -r requirements.txt
```
3. Run the script to fix the exported apkg file before importing them to anki.
```sh
./fix_mn_anki_exports.py fix /path/to/apkg
```
