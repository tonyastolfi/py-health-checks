import os.path as path
import re
import requests
import sys
import subprocess

import RPi.GPIO as GPIO

NEURIO_IPADDR = '192.168.1.109'
NEURIO_ID = '0x0000C47F5106CC81'


def check_enabled_file():
    assert path.exists("/var/lib/health_check/enabled")


def check_wifi_is_up():
    lines = subprocess.check_output(['ip', 'a']).decode('utf-8').split('\n')
    matches = [l for l in lines if re.match(' *inet \d+\.\d+\.\d+\.\d+.*wlan\d', l)]
    assert matches
    return matches


def check_neurio_device_reachable():
    r = requests.get(f"http://{NEURIO_IPADDR}/current-sample")
    assert r.ok
    sample = r.json()
    assert sample
    assert sample['sensorId'] == NEURIO_ID
    return ('timestamp', sample['timestamp'])
        

def check_neurio_exporter_running():
    r = requests.get("http://localhost:5000/metrics")
    assert r.ok
    assert len(r.text) > 0


def check_prometheus_metrics():
    r = requests.get("http://localhost:9090/metrics")
    assert r.ok
    assert len(r.text) > 0


def check_prometheus_targets():
    r = requests.get("http://localhost:9090/api/v1/targets")
    assert r.ok
    targets = r.json()
    assert all([t['health'] == 'up' for t in targets['data']['activeTargets']]), f"{targets}"


def check_prometheus_data_recent_updates():
    lines = subprocess.check_output(
        ['find', '/media/usb0/prometheus/data', '-mmin', '1']
    ).decode('utf-8').split('\n')
    assert lines
    return lines


def check_prometheus_disk_not_full():
    rows = [tuple(re.split(' +', line)) for line in 
        subprocess.check_output(['df', '-h']).decode('utf-8').split('\n')        
    ]
    assert rows
    mountpoint = rows[0].index('Mounted')
    use_percent = rows[0].index('Use%')
    rows = [rec for rec in rows if (
        len(rec) >= mountpoint and rec[mountpoint].startswith('/media/usb0')
    )]
    assert all((float(rec[use_percent].strip('%')) < 80 for rec in rows))
    return rows

    
checks = [
    ('health check enabled file exists', check_enabled_file),
    ('wifi address', check_wifi_is_up),
    ('neurio device', check_neurio_device_reachable),
    ('neurio exporter', check_neurio_exporter_running),
    ('prometheus server', check_prometheus_metrics),
    ('prometheus targets', check_prometheus_targets),
    ('prometheus data files updated in the last minute', check_prometheus_data_recent_updates),
    ('prometheus disk less than 80% full', check_prometheus_disk_not_full),
]

# Set up the GPIOs
#
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(17, GPIO.OUT, initial=GPIO.HIGH)

# Run the checks
#
all_ok = True
for name, check_fn in checks:
    result = None
    ok = False
    print(f"Checking {name}...")
    try:
        result = check_fn()
        ok = True
    except:
        all_ok = False
        result = sys.exc_info()
        
    print(f"  {'PASS' if ok else 'FAIL'}: {result if result is not None else '(no details)'}")


if all_ok:
    GPIO.output(17, 1)
else:
    GPIO.output(17, 0)
