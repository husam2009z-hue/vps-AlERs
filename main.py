#!/usr/bin/env python3
# make_package.py — ينشئ حزمة temp-vps-selenium ويضغطها zip

import sys
import stat
import zipfile
import shutil
from pathlib import Path

ROOT_NAME = sys.argv[1] if len(sys.argv) > 1 else "andreo-vps"
ROOT = Path(ROOT_NAME).resolve()
ZIP_PATH = ROOT.parent / (ROOT_NAME + ".zip")


# ============================================================
# 1. workflow.yml
# ============================================================
WORKFLOW_YML = "\n".join([
    "name: temp-vps-selenium",
    "on:",
    "  workflow_dispatch:",
    "  schedule:",
    "    - cron: '0 */5 * * *'",
    "",
    "concurrency:",
    "  group: temp-vps",
    "  cancel-in-progress: false",
    "",
    "jobs:",
    "  vps:",
    "    runs-on: ubuntu-latest",
    "    timeout-minutes: 350",
    "    steps:",
    "      - uses: actions/checkout@v4",
    "",
    "      - uses: actions/setup-python@v5",
    "        with:",
    "          python-version: '3.12'",
    "",
    "      - uses: browser-actions/setup-chrome@v1",
    "        with:",
    "          chrome-version: stable",
    "",
    "      - name: Install deps",
    "        run: |",
    "          sudo apt-get update -qq",
    "          sudo apt-get install -y -qq libnss3 libgconf-2-4 libxss1 \\",
    "            libappindicator1 libindicator7 rsync openssl curl unzip",
    "          pip install --quiet selenium webdriver-manager",
    "",
    "      - name: Install rclone",
    "        run: curl -s https://rclone.org/install.sh | sudo bash -s -- --version 1.65.0",
    "",
    "      - name: Configure rclone",
    "        env:",
    "          RCLONE_CONF: ${{ secrets.RCLONE_CONF }}",
    "        run: |",
    "          mkdir -p ~/.config/rclone",
    "          echo \"$RCLONE_CONF\" > ~/.config/rclone/rclone.conf",
    "          chmod 600 ~/.config/rclone/rclone.conf",
    "",
    "      - name: Restore previous state",
    "        env:",
    "          BACKUP_PASS: ${{ secrets.BACKUP_PASS }}",
    "        run: |",
    "          LATEST=$(rclone lsf your-remote:backups/actions/ --files-only \\",
    "            --include 'session-*.enc' | sort | tail -1 || true)",
    "          if [ -n \"$LATEST\" ]; then",
    "            rclone copy \"your-remote:backups/actions/$LATEST\" /tmp/ --quiet",
    "            openssl enc -d -aes-256-cbc -pbkdf2 \\",
    "              -in \"/tmp/$LATEST\" -out /tmp/restore.tar.gz \\",
    "              -pass pass:\"$BACKUP_PASS\"",
    "            tar -xzf /tmp/restore.tar.gz -C /home/runner/work/ 2>/dev/null || true",
    "            echo \"Restored: $LATEST\"",
    "          else",
    "            echo \"No previous state.\"",
    "          fi",
    "",
    "      - name: Start backup daemon",
    "        env:",
    "          BACKUP_PASS: ${{ secrets.BACKUP_PASS }}",
    "        run: |",
    "          chmod +x scripts/backup-daemon.sh",
    "          nohup scripts/backup-daemon.sh > /tmp/backup.log 2>&1 &",
    "",
    "      - name: Run Selenium job",
    "        env:",
    "          BACKUP_PASS: ${{ secrets.BACKUP_PASS }}",
    "        run: |",
    "          chmod +x scripts/final-escape.sh",
    "          trap 'scripts/final-escape.sh' EXIT SIGTERM SIGINT",
    "          python scripts/selenium-job.py",
    "",
    "      - name: Final escape",
    "        if: always()",
    "        env:",
    "          BACKUP_PASS: ${{ secrets.BACKUP_PASS }}",
    "        run: scripts/final-escape.sh",
    "",
    "      - name: Print backup log",
    "        if: always()",
    "        run: tail -50 /tmp/backup.log || true",
    "",
])


# ============================================================
# 2. backup-daemon.sh
# ============================================================
BACKUP_DAEMON_SH = "\n".join([
    "#!/bin/bash",
    "set -u",
    "",
    "BACKUP_PASS=\"${BACKUP_PASS:?missing BACKUP_PASS}\"",
    "REMOTE=\"your-remote\"",
    "REMOTE_PATH=\"backups/actions\"",
    "INTERVAL=60",
    "COLLECT_DIR=\"/tmp/collect\"",
    "WORK=\"/home/runner/work\"",
    "",
    "mkdir -p \"$COLLECT_DIR\"",
    "cleanup() { rm -rf \"$COLLECT_DIR\"/* /tmp/session-backup.tar.gz /tmp/session-backup.enc; }",
    "trap cleanup EXIT",
    "",
    "while true; do",
    "  rsync -a --delete \\",
    "    --include='*/' \\",
    "    --include='*.py' --include='*.json' --include='*.txt' \\",
    "    --include='*.csv' --include='*.log' --include='*.db' \\",
    "    --include='*.pkl' --include='*.cookies' \\",
    "    --exclude='*' \\",
    "    \"$WORK/\" \"$COLLECT_DIR/\" 2>/dev/null",
    "",
    "  if [ -z \"$(ls -A \"$COLLECT_DIR\")\" ]; then",
    "    sleep \"$INTERVAL\"; continue",
    "  fi",
    "",
    "  tar -czf /tmp/session-backup.tar.gz -C \"$COLLECT_DIR\" . 2>/dev/null || {",
    "    sleep \"$INTERVAL\"; continue",
    "  }",
    "",
    "  openssl enc -aes-256-cbc -pbkdf2 -salt \\",
    "    -in /tmp/session-backup.tar.gz \\",
    "    -out /tmp/session-backup.enc \\",
    "    -pass pass:\"$BACKUP_PASS\" 2>/dev/null || {",
    "    sleep \"$INTERVAL\"; continue",
    "  }",
    "",
    "  STAMP=$(date +%Y%m%d-%H%M%S)-$RANDOM",
    "  rclone copy /tmp/session-backup.enc \\",
    "    \"$REMOTE:$REMOTE_PATH/session-$STAMP.enc\" \\",
    "    --no-traverse --quiet 2>/dev/null",
    "",
    "  COUNT=$(rclone lsf \"$REMOTE:$REMOTE_PATH/\" --files-only \\",
    "    --include 'session-*.enc' | wc -l)",
    "  if [ \"$COUNT\" -gt 20 ]; then",
    "    rclone lsf \"$REMOTE:$REMOTE_PATH/\" --files-only \\",
    "      --include 'session-*.enc' | sort | head -n $((COUNT - 20)) | \\",
    "      while read -r f; do",
    "        rclone deletefile \"$REMOTE:$REMOTE_PATH/$f\" --quiet 2>/dev/null",
    "      done",
    "  fi",
    "",
    "  rm -f /tmp/session-backup.tar.gz /tmp/session-backup.enc",
    "  echo \"[$(date +%T)] shipped session-$STAMP.enc\"",
    "  sleep \"$INTERVAL\"",
    "done",
    "",
])


# ============================================================
# 3. final-escape.sh
# ============================================================
FINAL_ESCAPE_SH = "\n".join([
    "#!/bin/bash",
    "set -u",
    "",
    "BACKUP_PASS=\"${BACKUP_PASS:-}\"",
    "REMOTE=\"your-remote\"",
    "REMOTE_PATH=\"backups/actions/final\"",
    "WORK=\"/home/runner/work\"",
    "",
    "[ -z \"$BACKUP_PASS\" ] && exit 0",
    "",
    "tar -czf /tmp/final.tar.gz -C \"$WORK\" . 2>/dev/null || exit 0",
    "",
    "openssl enc -aes-256-cbc -pbkdf2 -salt \\",
    "  -in /tmp/final.tar.gz \\",
    "  -out /tmp/final.enc \\",
    "  -pass pass:\"$BACKUP_PASS\" 2>/dev/null || exit 0",
    "",
    "STAMP=$(date +%Y%m%d-%H%M%S)-$RANDOM",
    "rclone copy /tmp/final.enc \\",
    "  \"$REMOTE:$REMOTE_PATH/final-$STAMP.enc\" \\",
    "  --no-traverse --quiet 2>/dev/null",
    "",
    "echo \"[$(date +%T)] final escape: final-$STAMP.enc\"",
    "rm -f /tmp/final.tar.gz /tmp/final.enc",
    "",
])


# ============================================================
# 4. selenium-job.py
# ============================================================
SELENIUM_JOB_PY = "\n".join([
    "import json",
    "import pickle",
    "import time",
    "import logging",
    "from pathlib import Path",
    "",
    "from selenium import webdriver",
    "from selenium.webdriver.chrome.options import Options",
    "",
    "logging.basicConfig(",
    "    level=logging.INFO,",
    "    format='%(asctime)s [%(levelname)s] %(message)s',",
    "    handlers=[logging.FileHandler('/tmp/selenium.log'), logging.StreamHandler()],",
    ")",
    "log = logging.getLogger('job')",
    "",
    "COOKIE_FILE = Path('/home/runner/work/cookies.pkl')",
    "STATE_DIR = Path('/home/runner/work/state')",
    "STATE_DIR.mkdir(parents=True, exist_ok=True)",
    "",
    "",
    "def build_driver():",
    "    o = Options()",
    "    o.add_argument('--headless=new')",
    "    o.add_argument('--no-sandbox')",
    "    o.add_argument('--disable-dev-shm-usage')",
    "    o.add_argument('--disable-gpu')",
    "    o.add_argument('--window-size=1920,1080')",
    "    o.add_argument('--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')",
    "    o.add_experimental_option('excludeSwitches', ['enable-automation'])",
    "    o.add_experimental_option('useAutomationExtension', False)",
    "    return webdriver.Chrome(options=o)",
    "",
    "",
    "def load_cookies(d):",
    "    if not COOKIE_FILE.exists():",
    "        return",
    "    try:",
    "        with COOKIE_FILE.open('rb') as f:",
    "            for c in pickle.load(f):",
    "                try:",
    "                    d.add_cookie(c)",
    "                except Exception:",
    "                    pass",
    "        log.info('cookies loaded')",
    "    except Exception as e:",
    "        log.warning('cookie load: %s' % e)",
    "",
    "",
    "def save_cookies(d):",
    "    try:",
    "        with COOKIE_FILE.open('wb') as f:",
    "            pickle.dump(d.get_cookies(), f)",
    "        log.info('cookies saved')",
    "    except Exception as e:",
    "        log.warning('cookie save: %s' % e)",
    "",
    "",
    "def main():",
    "    log.info('boot')",
    "    d = build_driver()",
    "    try:",
    "        d.get('https://example.com')",
    "        load_cookies(d)",
    "        d.refresh()",
    "        log.info('title: %s' % d.title)",
    "        (STATE_DIR / 'last_state.json').write_text(",
    "            json.dumps({'title': d.title, 'url': d.current_url, 'ts': time.time()}, indent=2)",
    "        )",
    "        for i in range(60):",
    "            time.sleep(30)",
    "            d.get('https://example.com')",
    "            log.info('tick %d' % i)",
    "    except KeyboardInterrupt:",
    "        log.info('interrupted')",
    "    finally:",
    "        save_cookies(d)",
    "        try:",
    "            d.quit()",
    "        except Exception:",
    "            pass",
    "        log.info('shutdown')",
    "",
    "",
    "if __name__ == '__main__':",
    "    main()",
    "",
])


# ============================================================
# 5. README.md
# ============================================================
README_MD = "\n".join([
    "# temp-vps-selenium",
    "",
    "## setup",
    "1. `git init && git add . && git commit -m init`",
    "2. `git remote add origin <repo-url> && git push -u origin main`",
    "3. Settings -> Secrets -> Actions:",
    "   - `RCLONE_CONF` = full rclone.conf contents",
    "   - `BACKUP_PASS` = strong passphrase",
    "4. Edit `your-remote` in:",
    "   - `.github/workflows/vps.yml`",
    "   - `scripts/backup-daemon.sh`",
    "   - `scripts/final-escape.sh`",
    "5. Actions -> Run workflow.",
    "",
    "## restore locally",
    "```bash",
    "export BACKUP_PASS='your-pass'",
    "LATEST=$(rclone lsf your-remote:backups/actions/ --files-only --include 'session-*.enc' | sort | tail -1)",
    "rclone copy \"your-remote:backups/actions/$LATEST\" /tmp/ --quiet",
    "openssl enc -d -aes-256-cbc -pbkdf2 -in \"/tmp/$LATEST\" -out /tmp/restore.tar.gz -pass pass:\"$BACKUP_PASS\"",
    "mkdir -p restored && tar -xzf /tmp/restore.tar.gz -C restored",
    "```",
    "",
])


# ============================================================
# FILES MAP
# ============================================================
FILES = {
    ".github/workflows/vps.yml": (WORKFLOW_YML, False),
    "scripts/backup-daemon.sh": (BACKUP_DAEMON_SH, True),
    "scripts/final-escape.sh": (FINAL_ESCAPE_SH, True),
    "scripts/selenium-job.py": (SELENIUM_JOB_PY, False),
    "README.md": (README_MD, False),
}


def write_file(rel, content, executable):
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def build_package():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)

    for rel, (content, executable) in FILES.items():
        write_file(rel, content, executable)

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in ROOT.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(ROOT.parent))

    size_kb = ZIP_PATH.stat().st_size / 1024
    print("[+] folder :", ROOT)
    print("[+] zip    :", ZIP_PATH, "(" + str(round(size_kb, 1)) + " KB)")
    print("[+] files  :")
    for rel in FILES:
        print("      -", rel)


if __name__ == "__main__":
    build_package()
