# Miva PDF Downloader

Downloads the PDFs from your Miva courses into folders, one per week, ready to upload to NotebookLM (or anything else).

It only downloads what you can already open when logged in as yourself. You log in yourself in a browser window; the tool never sees or stores your password.

## Disclaimer

This is an independent, unofficial tool. It is not made, endorsed or supported by Miva Open University, Moodle, Google or any other company named here.

- **Purpose.** This tool exists only to automate a tedious manual process: saving, one by one, the course files you already have access to. It is not intended for any malpractice, cheating, circumventing access controls, or violation of academic or professional ethics, and it must not be used for any of those things.
- **Personal use only.** It logs in as you and downloads only what your own account can already open. Use it only for your own study, and only for material you are allowed to download.
- **You are responsible for how you use it.** Check Miva's terms of use and your university's rules on automated tools before running it. The author is not responsible for any account restriction, suspension or other consequence that results from using it.
- **Course material belongs to its owners.** Downloaded files are copyrighted by Miva and the course authors. Don't share, resell or republish them.
- **No warranty.** The tool is provided "as is", without warranty of any kind. The author is not liable for any loss, damage or data problems arising from its use.
- **Keep your login private.** The `browser_profile` folder holds your saved sign-in. Never share it.

## Quick start (Windows)

1. Install Python from https://www.python.org/downloads/ if you don't have it. On the first screen of the installer, tick **"Add python.exe to PATH"**.
2. Download **Miva-PDF-Downloader.zip** from the [Releases page](https://github.com/brainy0x/miva-pdf-downloader/releases/latest), then unzip it somewhere (for example your Documents).
3. Double-click **Run Miva Downloader.bat**.
   - The first time it installs a small Python package. It uses the Chrome, Edge or Brave you already have, and only downloads a browser (a few minutes) if none is found.
4. A browser window opens. Log in to Miva there.
5. Back in the black window you'll see a numbered list of your courses. Type the numbers you want (for example `1 3`) or `all`, then press Enter.
6. Wait. It prints a line for every item. When it says Summary, your PDFs are in the **downloads** folder, one folder per course and per week.

Run it again later to pick up new weeks. Files you already have are skipped.

## Other ways to run it

From PowerShell in this folder:

```
python download_pdfs.py            # shows your courses, you pick
python download_pdfs.py all        # every course
python download_pdfs.py 566 570    # specific course ids
```

A course id is the number after `id=` in the course page's address bar.

## What it downloads

- PDFs uploaded as files or folders
- "(PDF)" and reading links that point to a PDF or a Google Drive / Google Docs file

It does not download videos, quizzes, forms or the interactive lessons. Those links are listed in `external_links.txt` inside each course folder.

## If something goes wrong

The tool says what kind of problem it hit:

- **LOGIN:** your session expired or you can't open that item. Run it again and log in.
- **Google Drive wants a Google login:** in the browser window it opened, open drive.google.com in a new tab, sign in with the Google account you use for Miva, then run again. You only do this once.
- **NETWORK / SERVER:** the internet or Miva itself is slow. It retries and rests by itself; if it stops, just run it again later.
- **Nothing found:** it saves `debug_course_<id>.html`. Send that file to whoever maintains this tool.

## Please be considerate

- The tool waits a few seconds between requests on purpose. Miva slows down clients that go too fast, so please don't change that.
- Check your university's rules on automated tools before sharing it widely. It only downloads your own course material.
- The `browser_profile` folder holds your saved Google sign-in. Don't share it with anyone (don't zip it up).

## Notes

- Windows only for the double-click launcher. On Mac/Linux install Python, then run `pip install playwright`, then `python download_pdfs.py`.
- Built for Miva's Moodle (lms.miva.university).
