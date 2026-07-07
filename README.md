# SGB Minimal HTML

> **Minimal text-only HTML editions of the Stadt.Geschichte.Basel chapters**

**sgb-minimal-html** converts the chapter PDFs of the nine-volume book series [Stadt.Geschichte.Basel](https://www.stadtgeschichtebasel.ch/) (Christoph Merian Verlag) into minimal, text-only HTML editions — no images, no styling, just semantically structured text with linked endnotes — and publishes them as HTML publication formats on the Open Monograph Press instance [emono.unibas.ch](https://emono.unibas.ch/stadtgeschichtebasel/).

[![GitHub issues](https://img.shields.io/github/issues/Stadt-Geschichte-Basel/sgb-minimal-html.svg)](https://github.com/Stadt-Geschichte-Basel/sgb-minimal-html/issues)
[![GitHub stars](https://img.shields.io/github/stars/Stadt-Geschichte-Basel/sgb-minimal-html.svg)](https://github.com/Stadt-Geschichte-Basel/sgb-minimal-html/stargazers)
[![Code license](https://img.shields.io/github/license/Stadt-Geschichte-Basel/sgb-minimal-html.svg)](LICENSE-AGPL.md)
[![Data license](https://img.shields.io/badge/Data%20License-CC%20BY--NC%204.0-blue.svg)](LICENSE-CCBYNC.md)

<!-- [![DOI](https://zenodo.org/badge/GITHUB_REPO_ID.svg)](https://zenodo.org/badge/latestdoi/ZENODO_RECORD) -->

## Features

- 📖 **79 chapter editions** across all nine volumes, one self-contained HTML file per chapter
- 🔤 **Typography-driven extraction** — every PDF line is classified by the series' font system (body text, headings, lead paragraphs, sidebar stories, endnotes, pull quotes, captions)
- 🔗 **Linked endnotes** with back-references; footnote markers are distinguished from isotope superscripts like ¹⁴C
- 📑 **Appendix mode** for bibliographies, image credits, registers, and author notes with hanging-indent entries
- 🧹 **Clean text** — de-hyphenation, paragraphs that flow across page breaks and around floated boxes, figure anchors removed
- 🏷️ **Rich metadata** from the OMP API: `citation_*` meta tags, DOI, pages, authors, CC license
- ✅ **Quality checks** — word-count ratios against the raw PDF text and marker/endnote consistency per chapter
- 🚀 **Idempotent publishing** to Open Monograph Press via its REST API

## Documentation

**📖 [Full Documentation](https://dokumentation.stadtgeschichtebasel.ch/sgb-minimal-html/)**

- [Overview and Quick Start](https://dokumentation.stadtgeschichtebasel.ch/sgb-minimal-html/)
- [Extraction Methodology](https://dokumentation.stadtgeschichtebasel.ch/sgb-minimal-html/methodology.html)

## Data

- `html/volume-0X/sgb-XX.YY-NNNNNN.html` — the generated chapter editions, named by their DOI suffix (e.g. [`10.21255/sgb-01.01-439115`](https://doi.org/10.21255/sgb-01.01-439115)). They are published on [emono.unibas.ch](https://emono.unibas.ch/stadtgeschichtebasel/) as HTML publication formats alongside the PDFs.
- `pdf/dois.txt` — the DOIs of all volumes and chapters. The chapter PDFs themselves are not part of this repository; they are available open access via their DOIs.

The book content is © Stadt.Geschichte.Basel / Christoph Merian Verlag and licensed under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). Full bibliographic records: see the [catalog](https://emono.unibas.ch/stadtgeschichtebasel/catalog).

## Installation

This project uses [uv](https://docs.astral.sh/uv/) for dependency management:

```bash
pip install uv
uv sync
```

## Configuration

Create a `.env` file:

```bash
cp example.env .env
```

Edit it with the API token of your emono.unibas.ch user (OMP user profile → API Key):

```env
APIKEY=YOUR_OMP_API_TOKEN
```

## Usage

Place the chapter PDFs under `pdf/volume-0X/chapters/` (named by DOI suffix), then:

```bash
# Convert all chapter PDFs to minimal HTML under html/
uv run sgb-html convert

# Convert a single chapter by DOI
uv run sgb-html convert --doi 10.21255/sgb-01.01-439115

# QA report: extraction coverage and footnote consistency per chapter
uv run sgb-html check

# Attach the HTML files to each volume's HTML publication format in OMP
uv run sgb-html upload --dry-run   # print planned API calls first
uv run sgb-html upload             # idempotent; skips already attached files
uv run sgb-html upload --replace   # delete and re-upload existing galleys
```

## Development

```bash
# Tests (100% coverage required on the core modules)
uv run pytest --cov --cov-fail-under=100

# Lint, format, and type check
uv run ruff check .
uv run ruff format .
uv run ty check

# Docs site
quarto preview
```

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details, and note our [Code of Conduct](CODE_OF_CONDUCT.md).

## License

- **Code**: [GNU Affero General Public License v3.0](LICENSE-AGPL.md)
- **Content** (generated HTML editions): [Creative Commons Attribution-NonCommercial 4.0 International](LICENSE-CCBYNC.md), © Stadt.Geschichte.Basel / Christoph Merian Verlag
