# Running the history recorder on the droplet

The recorder (`scripts/record_history.py`, see `docs/history.md`) reads the files published at data.borneosky.com every hour and
appends what is new to a SQLite file. It needs only Python, `requests` and (for the backup) `boto3`: no numpy, no keys for the
upstream sources. These steps are for the Ubuntu droplet, run as the `deploy` user with `sudo`.

1. A user with no login, and the code (this repository is public, so no login is needed to clone it):

       sudo adduser --system --group --home /var/lib/borneosky --shell /usr/sbin/nologin borneo
       sudo apt-get install -y python3-venv git sqlite3
       sudo git clone https://github.com/ugnateam-bro/borneosky-data.git /opt/borneosky-data

2. A small Python environment (only the two packages it needs):

       cd /opt/borneosky-data && sudo python3 -m venv .venv && sudo .venv/bin/pip install requests boto3

3. The settings file, readable by root only (the R2 lines can wait until the backup is set up):

       sudo mkdir -p /etc/borneosky && sudo install -m 600 /dev/null /etc/borneosky/history.env
       sudo nano /etc/borneosky/history.env        # HISTORY_DB=/var/lib/borneosky/history.db

4. The timers:

       sudo cp /opt/borneosky-data/deploy/*.service /opt/borneosky-data/deploy/*.timer /etc/systemd/system/
       sudo systemctl daemon-reload
       sudo systemctl start borneosky-history.service        # first run: about 25,000 rows
       sudo journalctl -u borneosky-history -n 30 --no-pager
       sudo systemctl enable --now borneosky-history.timer

5. The backup (after creating a PRIVATE R2 bucket and an API token limited to it; put R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
   R2_SECRET_ACCESS_KEY and R2_HISTORY_BUCKET in `/etc/borneosky/history.env`):

       sudo systemctl start borneosky-history-backup.service && sudo journalctl -u borneosky-history-backup -n 10 --no-pager
       sudo systemctl enable --now borneosky-history-backup.timer

Check on it any time: `sudo -u borneo sqlite3 /var/lib/borneosky/history.db "select dataset,result,rows_added,started_utc from runs order by id desc limit 12"`
and `systemctl list-timers 'borneosky-*'`. To update the code: `cd /opt/borneosky-data && sudo git pull`.
