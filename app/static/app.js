'use strict';
// Only changed forms prompt on navigation; successful submissions leave freely.
let dirty = false;
document.querySelectorAll('form[data-dirty]').forEach(form => {
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('change', () => { dirty = true; });
});
window.addEventListener('beforeunload', event => {
  if (dirty) { event.preventDefault(); event.returnValue = ''; }
});
document.querySelectorAll('form[method="post"]').forEach(form => {
  form.addEventListener('submit', event => {
    if (form.dataset.submitting) { event.preventDefault(); return; }
    dirty = false;
    form.dataset.submitting = 'true';
    form.querySelectorAll('button[type="submit"]').forEach(button => {
      button.disabled = true;
      button.dataset.originalText = button.textContent;
      button.textContent = '저장 중…';
    });
  });
});
window.addEventListener('pageshow', () => {
  document.querySelectorAll('form').forEach(form => delete form.dataset.submitting);
  document.querySelectorAll('button[data-original-text]').forEach(button => {
    button.disabled = false; button.textContent = button.dataset.originalText;
  });
});
document.getElementById('form-errors')?.focus();
const inspection = document.getElementById('inspection-form');
if (inspection) {
  const update = () => {
    const fields = [...inspection.querySelectorAll('.check-result')];
    const completed = fields.filter(field => field.value).length;
    const issue = fields.some(field => field.value === 'issue') || inspection.elements.impact_water_result.value === 'yes';
    document.getElementById('progress-text').textContent = `${completed} / 4`;
    document.getElementById('progress-bar').style.width = `${completed * 25}%`;
    document.querySelector('[role="progressbar"]').setAttribute('aria-valuenow', completed);
    document.getElementById('issue-warning').hidden = !issue;
    inspection.elements.notes.required = issue;
  };
  inspection.addEventListener('change', update); update();
}
const period = document.getElementById('filter-period');
if (period) {
  const update = () => {
    const custom = period.value === 'custom';
    document.getElementById('custom-dates').hidden = !custom;
    document.querySelectorAll('#custom-dates input').forEach(input => { input.required = custom; });
  };
  period.addEventListener('change', update); update();
}
const copyButton = document.getElementById('copy-address');
copyButton?.addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(copyButton.dataset.address); copyButton.textContent = '주소를 복사했습니다'; }
  catch { copyButton.textContent = '복사할 주소를 아래에서 선택해주세요'; window.prompt('주소 복사', copyButton.dataset.address); }
});
const locationButton = document.getElementById('my-location');
locationButton?.addEventListener('click', () => {
  const status = document.getElementById('location-status');
  if (!navigator.geolocation) { status.textContent = '현재 위치를 지원하지 않습니다. ERICA 주소 기준 위치를 유지합니다.'; return; }
  status.textContent = '현재 위치를 확인하는 중입니다.';
  navigator.geolocation.getCurrentPosition(() => {
    status.textContent = '현재 위치를 확인했습니다. 정확한 본관 좌표가 미확인되어 거리는 표시하지 않습니다.';
  }, () => {
    status.textContent = '현재 위치 없이 ERICA 주소 기준 위치를 표시합니다.';
  }, {timeout:10000, maximumAge:60000});
});
const panel = document.getElementById('kakao-map');
if (panel) {
  const status = document.getElementById('map-status');
  let attempt = 0;
  let currentMap, currentCenter, resizeObserver;
  const initialize = () => {
    const run = ++attempt;
    const key = panel.dataset.key;
    const fail = message => {
      if (run !== attempt) return;
      panel.classList.add('failed');
      panel.replaceChildren();
      const placeholder = document.createElement('div');
      placeholder.className = 'map-placeholder';
      const title = document.createElement('b'); title.textContent = '지도를 표시하지 못했습니다';
      const text = document.createElement('p'); text.className = 'sub'; text.textContent = message;
      placeholder.append(title, text); panel.append(placeholder);
      status.textContent = message;
    };
    if (!key) { status.textContent = '카카오 지도 키가 설정되지 않았습니다. 주소와 외부 지도는 이용할 수 있습니다.'; return; }
    status.textContent = '카카오 지도를 불러오는 중입니다.';
    panel.classList.remove('failed');
    const timer = setTimeout(() => fail('지도 요청 시간이 초과되었습니다. 키의 도메인 등록과 네트워크를 확인한 뒤 다시 시도해주세요.'), 12000);
    const load = () => {
      if (run !== attempt) return;
      try {
        if (!window.kakao?.maps?.load) throw Error('sdk');
        window.kakao.maps.load(() => {
          if (run !== attempt) return;
          try {
            const maps = window.kakao.maps;
            const geocoder = new maps.services.Geocoder();
            geocoder.addressSearch(panel.dataset.address, (rows, result) => {
              if (run !== attempt) return;
              clearTimeout(timer);
              if (result !== maps.services.Status.OK || !rows?.length) {
                fail(result === maps.services.Status.ZERO_RESULT ? '주소 검색 결과가 없습니다. 외부 지도에서 확인해주세요.' : '주소 변환 요청에 실패했습니다. 키 권한·도메인과 네트워크를 확인해주세요.'); return;
              }
              try {
                resizeObserver?.disconnect();
                panel.replaceChildren();
                currentCenter = new maps.LatLng(Number(rows[0].y), Number(rows[0].x));
                currentMap = new maps.Map(panel, {center:currentCenter, level:4});
                new maps.Marker({map:currentMap, position:currentCenter});
                currentMap.setZoomable(false);
                resizeObserver = new ResizeObserver(() => { currentMap.relayout(); currentMap.setCenter(currentCenter); });
                resizeObserver.observe(panel);
                status.textContent = '캠퍼스 주소 기준 위치입니다. 정확한 본관 좌표는 미확인입니다.';
              } catch { fail('지도 초기화에 실패했습니다. JavaScript 키와 SDK 도메인 등록을 확인해주세요.'); }
            });
          } catch { clearTimeout(timer); fail('지도 인증 또는 초기화에 실패했습니다. JavaScript 키·SDK 도메인·서비스 사용 설정을 확인해주세요.'); }
        });
      } catch { clearTimeout(timer); fail('지도 SDK를 사용할 수 없습니다. JavaScript 키와 등록 도메인을 확인해주세요.'); }
    };
    if (window.kakao?.maps?.load) { load(); return; }
    document.getElementById('kakao-sdk')?.remove();
    const script = document.createElement('script');
    script.id = 'kakao-sdk'; script.async = true;
    script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(key)}&autoload=false&libraries=services`;
    script.onload = load;
    script.onerror = () => { clearTimeout(timer); fail('지도 SDK 로드에 실패했습니다. 네트워크와 JavaScript 키·도메인 등록을 확인해주세요.'); };
    document.head.append(script);
  };
  document.getElementById('map-retry')?.addEventListener('click', initialize);
  initialize();
}
const exportDialog = document.getElementById('export-confirm');
if (exportDialog) {
  document.querySelector('[data-export-dialog]').addEventListener('click', event => { event.preventDefault(); exportDialog.showModal(); });
  exportDialog.querySelector('[data-close-dialog]').addEventListener('click', () => exportDialog.close());
  exportDialog.querySelector('form').addEventListener('submit', () => exportDialog.close());
}
