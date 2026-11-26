# Chapter review rubric

You review one chapter. You compare the extracted HTML with the PDF pages. You report errors that the extractor caused.

## Rules for you

- Read only. Do not edit any file except the findings file.
- Write only `findings.json` in your pack folder, for example `.review/<doi>/findings.json`.
- Do not run git. Do not run `sgb-html`. Do not run other programs that write files.
- Do not fix the HTML. A person fixes the extractor.
- Report only errors that you can see. Do not report style opinions.
- Quote both sides of each error. Copy the HTML text and the PDF text exactly.
- If you cannot see the PDF text in the page image, do not report the error.

## Files in your pack folder

| File            | Content                                                                                            |
| --------------- | -------------------------------------------------------------------------------------------------- |
| `chapter.html`  | The extracted HTML. Review this file.                                                              |
| `pages/pNN.png` | One image for each PDF page.                                                                       |
| `blocks.tsv`    | The index of HTML blocks. Use it for locators such as `p#42`.                                      |
| `ledger.tsv`    | One row for each PDF line. It shows what the extractor kept or dropped.                            |
| `diff.json`     | Candidates from a text comparison of the HTML with the PDF text layer. Confirm or reject each one. |
| `lint.json`     | Static findings. Look at these places first.                                                       |
| `manifest.json` | Page count, word ratio, extractor warnings, flagged pages.                                         |

## Procedure

1. Read `manifest.json`, `lint.json`, and `diff.json`.
2. Read `chapter.html` and `blocks.tsv`.
3. Check each candidate in `diff.json` against the page image. Report it if the image shows an error. Do not report it if the HTML is right. The codes are:
   - `pdf-line-missing`: the extractor kept the line, but the HTML does not hold it.
   - `html-not-in-pdf`: the HTML holds text that the PDF text layer does not hold. Check for a changed word, a repeated sentence, or caption text.
   - `block-order`: a paragraph stands before text that comes earlier in the PDF.
   - `split-word`: a space inside a word.
   - `note-mismatch`: the number or text of an endnote differs from the PDF.
4. Open the image of every page. Do not skip a page. Work in batches of 6 pages.
5. For each page, do these steps:
   1. Read the page image. Read the text in the order of the book.
   2. Read the `ledger.tsv` rows of the page that the extractor kept.
   3. Find the matching text in `chapter.html`.
   4. Check that each sentence of the page is in the HTML, in the same order.
   5. Check that no sentence in the HTML for this page is missing from the page.
   6. Look for a space inside a word, a joined word, and a wrong hyphen.
6. After each batch, write the complete `findings.json`. Include all findings so far.
7. If your task names a page range, review only that range. Read the existing `findings.json` first. Keep its findings and add yours.

## Honest coverage

- Put a page in `pages_inspected` only if you opened its image.
- Put every other page in `pages_skipped`.
- A text match against `ledger.tsv` is not an inspection.
- Do not stop early. If you must stop, list all remaining pages in `pages_skipped`.

## What the extractor drops on purpose

Do not report these as lost text.

- Figure captions, table captions, and image credits.
- Running heads and page numbers.
- Pull quotes. They repeat text from the body.
- Text inside maps, charts, and photos.
- Author lines on the first page. The HTML takes authors from a database.

## What the HTML does on purpose

Do not report these as errors.

- A lead paragraph (`class="lead"`) at the start of a section.
- A note without a backlink. The marker is in a dropped caption.
- An `<aside>` for each sidebar.
- A soft hyphen that the PDF uses is removed from words.

## Checks

1. **Lost or duplicated text.** Find body text in the page image that the HTML does not hold. Find HTML text that the PDF does not hold. Use the dropped rows in `ledger.tsv`.
2. **Caption or label text in prose.** Find caption, chart, or map text inside a paragraph.
3. **Reading order.** Check that columns, sidebars, and paragraphs that cross a page keep the order of the book.
4. **Broken words, URLs, spacing.** Find a space inside a word or URL. Find two words that are joined. Find a wrong hyphen.
5. **Headings and lead.** Check the level and text of each heading. Check the opener lead and each mid-chapter lead.
6. **Endnotes.** Compare every endnote with the PDF. Check the number, the text, the page references, and the years. Check that each marker sits at the right word.
7. **Special characters.** Check quotation marks, dashes, thin spaces, and accented letters.

## Severity

- `blocker`: text is missing, duplicated, or in the wrong place so that the reader gets wrong content.
- `major`: a visible error in a sentence, for example a joined word or a wrong note.
- `minor`: a small error, for example one extra space.

## Output: `findings.json`

```json
{
	"doi": "sgb-08.05-264155",
	"findings": [
		{
			"severity": "major",
			"html_locator": "p#42",
			"quote_html": "exact text from the HTML",
			"quote_pdf": "exact text from the PDF page",
			"pdf_page": 12,
			"confidence": "high",
			"class_hint": "broken-word"
		}
	],
	"pages_inspected": [1, 2, 3],
	"pages_skipped": []
}
```

- `html_locator`: the block name from `blocks.tsv`, for example `p#42`.
- `confidence`: `high`, `medium`, or `low`.
- `class_hint` must be one of these values:
  - `lost-text`
  - `duplicated-text`
  - `caption-in-prose`
  - `reading-order`
  - `broken-word`
  - `spacing`
  - `heading-or-lead`
  - `endnote-alignment`
  - `special-character`
  - `other`
- Report one finding for each error. If the same error repeats, report the first 3 cases and say in `quote_html` how many more you saw.
