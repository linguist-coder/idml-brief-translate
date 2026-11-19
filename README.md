# IDML Brief Translate

A small Windows app that translates an InDesign IDML file from English to Japanese using a saved translation brief. For the default brief, the app checks B-01 through B-06 automatically, including the specified placement of the `UI Label` style. An OpenAI model reviews the free-text rules in the brief and checks for changes in meaning, omissions and additions. You review the findings, edit the translation, run the checks again and export an IDML to inspect in InDesign.

It was built by Linguist Coder to show this workflow on one four-page test document. It is not a CAT tool. The exported file still needs a language review and a layout check in InDesign.

## What it does

1. **Brief.** A saved list of translation rules with IDs (`B-01`, `B-02`, ...): terms, words to keep in English, writing number words as digits, a character style that belongs on a given word, and free-text instructions. The same brief is used for every run.
2. **Input.** You open an IDML file. Text is extracted with Okapi Tikal. The app is designed to refuse files with features it does not support (tracked changes, notes, conditional text, XML-tagged content, hyperlinks, endnotes, text variables outside cross-references and a few more) and shows the reason.
3. **Translate.** Claude translates paragraph by paragraph. Character styles, footnote references, cross-references and index markers are sent as tags. A tag may move inside its paragraph to follow Japanese word order. A missing, repeated, unknown or moved-out tag fails the check, and the paragraph is translated once more. If it still fails, the source paragraph is kept and the report lists it.
4. **Check.** The app checks tags, terms, words to keep, numbers and the style placement. An OpenAI model then reviews the free-text rules and three built-in checks (`M-meaning`, `M-omission`, `M-addition`). Findings with any other rule ID are discarded.
5. **Edit.** You edit the Japanese text. An edit that breaks a tag is refused. When you save an edit, the app automatically reruns its checks and marks the previous AI review as out of date. Select **Run AI review** to review all translated paragraphs again.
6. **Export.** The app rebuilds the IDML with Tikal and runs a structure check against the input (footnotes, cross-references, index markers, anchored objects, tables and character formatting; order inside a paragraph may change). If the check reports a problem, export is blocked. The HTML report has three sections: language and brief, codes and structure, and InDesign layout, which is always "not checked".

## What it does not do

- Languages other than English to Japanese, files other than IDML, systems other than Windows.
- Text on hidden layers, index topic names and cross-reference format text are left as they are (listed in the report).
- It does not check the layout. The structure check does not establish whether formatting is attached to the intended words; only placements named in the brief (B-02 in the default brief) are checked. Open the IDML in InDesign and look.
- It has been tried on one test document (`fixtures/tx40_ja_base.idml`, 4 pages, 33 paragraphs). Other IDML files may contain things it refuses or has not seen.

## What was tested

- One translation run on the test document with `claude-opus-5-5`: 28 paragraphs in about 15 seconds, no tag errors, no retries. Estimated API cost for that translation run: about $0.04 at the rates recorded on 2026-11-17. The review cost is not shown because the price of the review model was not recorded.
- The `UI Label` style ended up on the Japanese word for "green," the Strong style stayed on POWER, and the tracking override stayed on the phrase for "is ready". Checked in InDesign.
- Planted errors in a manually prepared translation of the same document, reviewed with `gpt-5.5-2026-04-23`: the app's checks and the AI review together found all 6 in the first set (app 2 of 2, AI 4 of 4) and all 7 in the second set (app 1 of 1, AI 6 of 6). The first set was used while writing the review prompt. The second was run once with the prompt unchanged. Both runs also flagged the same unaltered note, which used the plain form, and I counted it as a B-07 violation. In the second set, a paragraph with a planted error also received an additional finding of another type, which I judged valid. Both sets were small and deliberately constructed within the same test document. These results do not establish how often the checks would miss errors in other documents.
- In automated tests, removing a footnote, a cross-reference, a tracking override or a style definition blocked the export.
- The Windows zip was tried on one other PC without Python or Java installed.

## Download and run

Get `IDMLBriefTranslate-<version>-win64.zip` from [Releases](https://github.com/linguist-coder/idml-brief-translate/releases), unzip it and double-click `IDMLBriefTranslate.exe`. The app is not signed, so Windows SmartScreen may warn you. A console window opens and your browser shows the app. Close the console window to quit.

The app is free. API usage is paid separately: you need your own Anthropic key (translation, `claude-opus-5-5`) and OpenAI key (review, `gpt-5.5-2026-04-23`).

### Your keys and your text

- The app keeps API keys in memory while it runs and does not save them to disk, logs or the report.
- The app runs on your PC and accepts local connections only. For translation, it sends the paragraph text and the saved brief, including its term rules, to Anthropic. For review, it sends the source and translated paragraphs to OpenAI with the free-text rules and the built-in checks. Do not use it on documents you are not allowed to send to those services.

## Run from source

Windows, Python 3.12.

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-build.txt
```

Put a Java runtime in `vendor/jre` and Okapi 1.48.0 in `vendor/okapi` (or set `IDMLBRIEF_JRE` and `IDMLBRIEF_OKAPI`). The release zip uses the Eclipse Temurin 21.0.12.1+1 JRE and the Okapi 1.48.0 Windows distribution from okapiframework.org.

```
.venv\Scripts\python -m unittest discover -s tests
.venv\Scripts\python app.py
```

Build the zip: `sh tools/build.sh <out_dir> .venv/Scripts/python.exe` (Git Bash). Use a venv with only `requirements-build.txt` installed, because PyInstaller bundles what it finds.

## License

The code in this repository is MIT (see `LICENSE`). The release zip also contains a Java runtime (GPL v2 with the Classpath Exception), the Okapi Framework (Apache 2.0) and Python packages under their own licenses. See `THIRD_PARTY_NOTICES.txt` in the zip. The source of the bundled Java runtime is attached to each release.
