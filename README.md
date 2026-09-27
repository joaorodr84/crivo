# Winnower

Give it a list of keywords. It searches [Pixabay](https://pixabay.com) for each one, shows you
the candidates in your browser so you can pick the image you want, then resizes your picks to
one size and hands them back in a zip, with a `CREDITS.txt` naming every contributor.

It is for the job of "I have a hundred words and need one picture for each, all the same
size", where the searching, downloading, resizing and packaging are tedious and the choosing
is the only part that needs you.

```console
$ winnower run examples/keywords_sample.txt --size 256
Searching 12 keywords on Pixabay...
  1 / 12  apple
  ...
12 with results
Pick your images at http://127.0.0.1:52431/?t=...
Downloading 11 images...
Wrote winnower.zip with 11 images for 11 keywords, plus CREDITS.txt.
```

## You need your own Pixabay API key

Winnower does not ship, share or proxy a key. Everyone who runs it uses their own, which is
free: log in at <https://pixabay.com/api/docs/> and the key is shown in the parameters table.
Pixabay ties keys to accounts and rate limits them per key, so a shared key would not work
well for anyone.

Put it in the environment, or in a `.env` file in the folder you run Winnower from:

```console
$ cp .env.example .env        # then edit .env and paste your key
```

```text
PIXABAY_API_KEY=your_key_here
```

The key is deliberately not a command-line option: a flag ends up in your shell history and in
the process list that every user on the machine can read. `.env` is gitignored. Winnower never
writes the key into its cache, its output or an error message.

## Install

Python 3.10 or newer, on Windows, macOS or Linux.

```console
$ git clone https://github.com/joaorodr84/winnower.git
$ cd winnower
$ python -m venv .venv
$ .venv/bin/pip install .               # Windows: .venv\Scripts\pip install .
```

That installs a `winnower` command. (`python -m winnower` does the same thing.) If you
activate the virtual environment first, you can drop the `.venv/bin/` prefix.

## Use

```console
$ winnower run keywords.txt
```

Winnower searches every keyword, then prints an address and opens it in your browser. For each
keyword you can:

- **pick** an image by clicking it (click another to change your mind);
- **skip** the keyword;
- **search again** with a different term, if nothing shown is right;
- **load more results** without losing what you have already picked.

Every image shows its contributor's name and a link to its Pixabay page. When you press
**Finish**, Winnower downloads your picks, resizes them and writes the zip.

### Keywords

| Source | Example |
| --- | --- |
| A `.txt` file, one keyword per line | `winnower run keywords.txt` |
| A `.csv` file with a header row | `winnower run words.csv --column word` |
| Standard input | `cat keywords.txt \| winnower run -` |
| Directly on the command line | `winnower run -k apple -k "hot-dog \| hot dog"` |

Blank lines and lines starting with `#` are ignored. To name the file after one thing and
search for another, write `label | search term`:

```text
hot-dog | hot dog
```

gives you `hot-dog.png`, found by searching for "hot dog". In a CSV the label is read from a
column headed `keyword` or `label` (otherwise the first) and the search term from one headed
`term` or `search`; `--column` and `--term-column` choose other columns. Labels become file
names, so a label repeated in a different case is dropped and reported.

`examples/keywords_sample.txt` is a small list to try it on.

### Options

`winnower run --help` lists everything. The ones you are most likely to want:

| Option | What it does |
| --- | --- |
| `-n`, `--candidates N` | Images shown per keyword, 3 to 200 (default 5). Pixabay's smallest page is 3. |
| `--size WxH` | Output size (default `512x512`). |
| `--mode crop\|pad\|fit` | `crop` fills the size and cuts the overflow; `pad` fits inside and fills the rest; `fit` fits inside and stops, so sizes are not uniform. |
| `--format png\|jpeg\|webp` | Output format (default `png`, which keeps transparency). |
| `--background #RRGGBB` | The fill for `pad`, and what a JPEG is flattened onto. Default: transparent, or white for JPEG. |
| `--term-template '{term} icon'` | Wrap every search term, for example to find icons or flat illustrations. |
| `--image-type`, `--orientation`, `--category`, `--colors`, `--min-width`, `--min-height`, `--safesearch`, `--editors-choice`, `--order`, `--lang` | Pixabay's own search filters. |
| `--multiple` | Allow more than one image per keyword (`apple.png`, `apple-2.png`, ...). |
| `-o`, `--output ZIP` | Where to write the zip (default `winnower.zip`). It is not replaced unless you pass `--overwrite`. |
| `--work-dir DIR` | Search cache, downloaded originals and the saved session (default `.winnower`). |
| `--resume` / `--restart` | Continue the saved session, or throw an unfinished one away and start over. |
| `--no-browser` | Print the address instead of opening it. |

### Pausing and resuming

Your picks are saved as you make them, in `session.json` inside the work directory (`.winnower`
by default). Close the terminal, press Ctrl-C, or shut the laptop, and carry on later:

```console
$ winnower run --resume
```

Resuming reads the keywords from the saved session, so you give it none, and it does not search
again for anything already searched: the page comes back as you left it. Options that affect the
output (`--size`, `--mode`, `--format`, `-o`) are taken from the new command, so you can change
your mind about them.

Starting a new run while an unfinished session is saved is refused, so that a long afternoon of
picking is not thrown away by accident. Pass `--restart` when you mean to start over. A session
whose zip has been written is finished with, and the next run replaces it without asking.

A session file holds Pixabay's image addresses, which are meant for short-term display. Resuming
one that is more than a day old works, and warns you that a thumbnail or a download may no longer
load. The file never contains your API key.

### What you get

A zip of the resized images, each named after its keyword, and a `CREDITS.txt`:

```text
apple.png
  Keyword:      apple
  Pixabay page: https://pixabay.com/photos/...
  Contributor:  someone
```

### When something goes wrong

Nothing that can go wrong on the network or at Pixabay ends the run with a traceback.

- A keyword with **no results**, or whose search **failed**, is flagged on the page. Search
  again with another term, or skip it.
- If Pixabay **refuses your key**, the **rate limit is used up**, or Pixabay **cannot be
  reached three times in a row**, Winnower stops searching and says so. What it found is kept,
  the page still opens, and the remaining keywords show as not searched so you can retry them.
  A failing request is tried a few times with growing pauses and then given up on; Winnower
  does not keep looping in the background.
- A picked image that **cannot be downloaded** is left out of the zip and listed after the run.

Searches are cached for 24 hours, and downloaded originals are kept in the work directory, so
running the same list again costs no requests.

Exit codes: `0` done; `1` something you can fix (a bad option, a missing keyword file or key,
an output file that already exists); `3` the zip was written but some picks are missing from it;
`130` you pressed Ctrl-C.

## About Pixabay, and what you are agreeing to

Winnower is a small tool that talks to Pixabay's API on your behalf and is built to respect
[Pixabay's API terms](https://pixabay.com/api/docs/):

- **No permanent hotlinking.** Pixabay's image addresses are only used to show you the search
  results while you choose. Once you pick, the image is downloaded, and everything after that
  uses your local copy.
- **Attribution where results are shown.** Each candidate carries its contributor's name and a
  link to its Pixabay page, and the zip includes a `CREDITS.txt`.
- **Caching and rate limits.** Search results are cached for 24 hours, requests are held to
  Pixabay's limit of 100 per minute per key, and a rate-limit response is waited out.
- **Human-paced.** A person chooses every image. Winnower is not a way to bulk-download
  Pixabay, and it will not run searches in an unattended loop.

The [MIT licence](LICENSE) in this repository covers Winnower's **code only**. It says nothing
about the images you download: those are under the
[Pixabay Content License](https://pixabay.com/service/license/), which is what allows you to use
and modify them (resizing included), commercially or not, without attribution. It does not
cover everything that can appear in an image: some contain trademarks, logos or recognisable
people that carry rights Pixabay does not clear. Looking at what you pick is your
responsibility, above all for a commercial project.

The repository contains no Pixabay images and never will; please do not commit any.

## Status

Winnower is at version 0.x. The whole pipeline works and is covered by automated tests on
Windows, macOS and Linux, but those tests use a simulated Pixabay and never touch the network.
A full run against the live API on all three systems has not yet been done, so expect a rough
edge or two in how it meets the real service. Bug reports with the message Winnower printed are
welcome.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
