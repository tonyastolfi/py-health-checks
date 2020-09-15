import os.path as path
import re
import requests
import sys
import subprocess
import time
import traceback

import RPi.GPIO as GPIO

NEURIO_IPADDR = '192.168.1.109'
NEURIO_ID = '0x0000C47F5106CC81'

MAX_RETRIES = 3

checks = []

#----------------------------------------------------------------
# Decorator - attach this to a regular function to make it into a health check.
#

def health_check(name,
                 mitigation=None,
                 max_retries=3,
                 max_rechecks=10,
                 recheck_delay_sec=1,
                 retry_all_if_mitigated=True):   
    def decorator(check_fn):
        def wrapper():
            mitigated = False
            for retry in range(max_retries):
                for recheck in range(max_rechecks):
                    try:
                        detail_info = check_fn()
                        return (detail_info, mitigated and retry_all_if_mitigated)
                    except:
                        if mitigation is None or retry == max_retries - 1:
                            raise
                        
                        if recheck == 0:
                            assert mitigation is not None
                            mitigation()
                            mitigated = True

                        if recheck_delay_sec is not None:
                            time.sleep(recheck_delay_sec)
        
        checks.append((name, wrapper))
        return wrapper

    return decorator


#------------------------------------------------------------
# Mitigations
#

def netplan_apply():
    """
    Running `sudo netplan apply` will usually bring up the wifi after a disconnect.
    """
    subprocess.check_output(['netplan', 'apply'])

    
def restart_prometheus():
    """
    On boot, if prometheus starts too soon, it won't see the data disk mounted by 
    usbmount; this will show up as a failure in the recently updated prometheus data
    files check.  The mitigation is to kill prometheus so that systemd restarts it
    and it sees the correct mountpoint.
    """
    subprocess.check_output(['killall', 'prometheus'])

    
#------------------------------------------------------------
# Health Checks
#

@health_check(name='health check enabled file exists')
def check_enabled_file():
    assert path.exists("/var/lib/health_check/enabled")


@health_check(name='wifi address', mitigation=netplan_apply)
def check_wifi_is_up():
    lines = subprocess.check_output(['ip', 'a']).decode('utf-8').split('\n')
    matches = [l.strip() for l in lines if re.match(' *inet \d+\.\d+\.\d+\.\d+.*wlan\d', l)]
    assert matches
    return matches


@health_check(name='neurio device')
def check_neurio_device_reachable():
    r = requests.get(f"http://{NEURIO_IPADDR}/current-sample")
    assert r.ok
    sample = r.json()
    assert sample
    assert sample['sensorId'] == NEURIO_ID
    return ('timestamp', sample['timestamp'])
        

@health_check(name='neurio exporter')
def check_neurio_exporter_running():
    r = requests.get("http://localhost:5000/metrics")
    assert r.ok
    assert len(r.text) > 0


@health_check(name='prometheus server')
def check_prometheus_metrics():
    r = requests.get("http://localhost:9090/metrics")
    assert r.ok
    assert len(r.text) > 0


@health_check(name='prometheus targets')
def check_prometheus_targets():
    r = requests.get("http://localhost:9090/api/v1/targets")
    assert r.ok
    targets = r.json()
    assert all([t['health'] == 'up' for t in targets['data']['activeTargets']]), f"{targets}"


@health_check(name='prometheus data files updated in the last minute',
              mitigation=restart_prometheus)
def check_prometheus_data_recent_updates():
    lines = [l.strip() for l in subprocess.check_output(
        ['find', '/media/usb0/prometheus/data', '-mmin', '1']
    ).decode('utf-8').split('\n') if l.strip()]
    assert lines
    return lines


@health_check(name='prometheus disk less than 80% full')
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


@health_check(name='wifi country code')
def check_wifi_country_code():
    out = subprocess.check_output(['/usr/sbin/iw', 'reg', 'get']).decode('utf-8')
    assert 'country US: DFS-FCC' in out, out
    return [l.strip() for l in out.split('\n') if 'country' in l]


@health_check(name='wifi power management')
def check_wifi_power_save():
    out = subprocess.check_output(['/usr/sbin/iw', 'wlan0', 'get', 'power_save']).decode('utf-8')
    assert 'Power save: off'in out
    return out.strip()

#-----------------------------------------------------------------

# Set up the GPIOs
#
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(17, GPIO.OUT, initial=GPIO.HIGH)

# Run the checks
#
for i in range(MAX_RETRIES):
    all_ok = True
    retry_all = False
    failing = []
    for name, check_fn in checks:
        result = None
        ok = False
        print(f"\nChecking {name}...")
        try:
            (result, retry_all) = check_fn()
            if retry_all:
                print(f"    Mitigated after recheck; retrying all...")
                break;
            ok = True
        except:
            all_ok = False
            (cls, ex, tb) = sys.exc_info()
            result = f"{cls} {ex} {traceback.format_exc()}"
            failing.append(name)
        
        print(f"    {'PASS' if ok else 'FAIL'}: {result if result is not None else '(no details)'}")
        
    if not retry_all:
        break;


if all_ok:
    print(f"\n==#==========+==+=+=++=+++++++++++-+-+--+----- --- -- -  -  -   -")
    print("OVERALL: PASS")
    GPIO.output(17, 1)
else:
    print(f"\n==#==========+==+=+=++=+++++++++++-+-+--+----- --- -- -  -  -   -")
    print(f"OVERALL: FAIL {failing}")
    GPIO.output(17, 0)
