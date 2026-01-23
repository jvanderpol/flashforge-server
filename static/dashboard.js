function getIp() {
  const urlParams = new URLSearchParams(window.location.search);
  return urlParams.get('ip') || '';
}

async function refreshStream() {
  document.getElementById('printer-cam').src = `stream?ip=${getIp()}&?t=${new Date().getTime()}`;
}

async function updateDashboard() {
  const ip = getIp();
  const nozzleTempCurrent = document.getElementById('nozzle-temp-current');
  const nozzleTempTarget = document.getElementById('nozzle-temp-target');
  const bedTempCurrent = document.getElementById('bed-temp-current')
  const bedTempTarget = document.getElementById('bed-temp-target')
  const tempResponse = await fetch(`temp?ip=${ip}`)
  if (tempResponse.ok) {
    const temp = await tempResponse.json();
    nozzleTempCurrent.innerText = temp.nozzle.current
    nozzleTempTarget.innerText = temp.nozzle.target || '--';
    bedTempCurrent.innerText = temp.bed.current
    bedTempTarget.innerText = temp.bed.target || '--';
  } else {
    nozzleTempCurrent.innerText = '--'
    nozzleTempTarget.innerText = '--'
    bedTempCurrent.innerText = '--'
    bedTempTarget.innerText = '--'
  }

  const statusElement = document.getElementById('status');
  const percentTextElement = document.getElementById('percent-text');
  const progressBar = document.getElementById('progress-bar');
  const statusResponse = await fetch(`status?ip=${ip}`);
  if (statusResponse.ok) {
    const status = await statusResponse.json();
    const decodedStatus = {
      BUILDING_FROM_SD: {
        WAIT_ON_TOOL: {
          key: 'PREHEATING',
          text: 'Pre Heating'
        },
        MOVING: {
          key: 'PRINTING',
          text: 'Printing'
        },
        PAUSED: {
          key: 'PAUSED',
          text: 'Paused'
        },
        HOMING: {
          key: 'HOMING',
          text: 'Preparing'
        }
      },
      READY: {
        READY: {
          key: 'READY',
          text: 'Ready'
        },
        PAUSED: {
          key: 'DONE',
          text: 'Done'
        }
      },
      BUSY: {
        MOVING: {
          key: 'CANCELLING',
          text: 'Cancelling'
        }
      },
      BUILDING_COMPLETED: {
        PAUSED: {
          key: 'BUILDING_COMPLETED',
          text: 'Building Complete'
        }
      }
    } [status.status]?.[status.move_mode] || {
      key: 'UNKNOWN',
      text: status.status
    }

    statusElement.innerText = decodedStatus.text
    if (decodedStatus.key == 'PRINTING') {
      statusElement.style.color = 'var(--status-printing)';
    } else if (decodedStatus.key === 'PASUED') {
      statusElement.style.color = 'var(--status-paused)';
    } else {
      statusElement.style.color = null;
    }

    let percentText;
    let percentValue;
    if (decodedStatus.key == 'READY') {
      percentText = '--';
      percentValue = 0;
    } else {
      const progressResponse = await fetch(`progress?ip=${ip}`);
      if (progressResponse.ok) {
        const progress = await progressResponse.json();
        const unroundedPercentValue = (progress.printed_bytes.current / progress.printed_bytes.total) * 100;
        percentValue = Math.round(unroundedPercentValue * 10) / 10;
        percentText = percentValue + '%';
      } else {
        percentValue = 0;
        percentText = '--';
      }
    }
    percentTextElement.innerText = percentText;
    progressBar.style.width = percentValue + '%';
  } else {
    statusElement.innerText = 'OFFLINE'
    statusElement.style.color = null;
    percentTextElement.innerText = '--';
    progressBar.style.width = '0%';
  }
}

function toggleFullscreen() {
  const elem = document.getElementById('camera-wrapper');

  if (!document.fullscreenElement) {
    if (elem.requestFullscreen) {
      elem.requestFullscreen();
    } else if (elem.webkitRequestFullscreen) {
      /* Safari */
      elem.webkitRequestFullscreen();
    } else if (elem.msRequestFullscreen) {
      /* IE11 */
      elem.msRequestFullscreen();
    }
  } else {
    if (document.exitFullscreen) {
      document.exitFullscreen();
    }
  }
}

async function sendAction(action) {
  try {
    const response = await fetch(`${action}?ip=${getIp()}`, {
      method: 'POST'
    });

    if (response.ok) {
      alert(`${action} command sent successfully.`);
      updateDashboard();
    } else {
      const responseText = await response.text();
      alert(`${action} failed: ${responseText}`);
    }
  } catch (error) {
    console.error('Command failed:', error);
    alert('Failed to send command to printer.');
  }
}

function confirmAbort() {
  if (confirm("Are you sure you want to STOP the print? This cannot be undone.")) {
    sendAction('abort');
  }
}

window.onload = () => {
  updateDashboard();
  refreshStream();
};
setInterval(updateDashboard, 10000);

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    refreshStream();
  }
});