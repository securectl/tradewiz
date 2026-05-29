/* In-app feedback form — NPS / CSAT / ease + open text. */

let _fb = { nps: null, csat: null, ease: null };

function _fbBuildScales() {
    // NPS 0-10
    const nps = document.getElementById('fb-nps');
    if (nps && !nps.dataset.built) {
        nps.dataset.built = '1';
        for (let i = 0; i <= 10; i++) {
            const b = document.createElement('button');
            b.type = 'button'; b.className = 'fb-nps-btn'; b.textContent = i;
            b.onclick = () => { _fb.nps = i; nps.querySelectorAll('.fb-nps-btn').forEach(x => x.classList.toggle('on', x === b)); };
            nps.appendChild(b);
        }
    }
    // 1-5 scales (csat, ease)
    ['fb-csat', 'fb-ease'].forEach(id => {
        const el = document.getElementById(id);
        if (!el || el.dataset.built) return;
        el.dataset.built = '1';
        const key = id === 'fb-csat' ? 'csat' : 'ease';
        for (let i = 1; i <= 5; i++) {
            const b = document.createElement('button');
            b.type = 'button'; b.className = 'fb-scale-btn'; b.textContent = i;
            b.onclick = () => { _fb[key] = i; el.querySelectorAll('.fb-scale-btn').forEach(x => x.classList.toggle('on', x === b)); };
            el.appendChild(b);
        }
    });
}

function openFeedback() {
    _fbBuildScales();
    const ov = document.getElementById('fb-overlay');
    if (ov) ov.classList.add('open');
}
function closeFeedback() {
    const ov = document.getElementById('fb-overlay');
    if (ov) ov.classList.remove('open');
}

async function submitFeedback() {
    const msg = document.getElementById('fb-msg');
    const btn = document.getElementById('fb-submit');
    const payload = {
        nps: _fb.nps, csat: _fb.csat, ease: _fb.ease,
        valuable: (document.getElementById('fb-valuable') || {}).value || '',
        improve: (document.getElementById('fb-improve') || {}).value || '',
        email: (document.getElementById('fb-email') || {}).value || '',
    };
    if (payload.nps == null && payload.csat == null && payload.ease == null && !payload.improve.trim()) {
        if (msg) { msg.textContent = 'Please answer at least one question.'; msg.className = 'fb-msg fb-err'; }
        return;
    }
    if (btn) { btn.disabled = true; btn.textContent = 'Sending…'; }
    try {
        const resp = await fetch('/api/feedback', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (resp.ok) {
            const body = document.getElementById('fb-body');
            if (body) body.innerHTML = '<div class="fb-thanks">🎉<br>Thanks for your feedback!<br><span>It helps us make TradeWiz better.</span></div>';
            if (btn) btn.style.display = 'none';
            if (msg) msg.textContent = '';
            setTimeout(closeFeedback, 1600);
        } else {
            if (msg) { msg.textContent = (data && data.error) || 'Could not send.'; msg.className = 'fb-msg fb-err'; }
            if (btn) { btn.disabled = false; btn.textContent = 'Send feedback'; }
        }
    } catch (e) {
        if (msg) { msg.textContent = 'Network error — try again.'; msg.className = 'fb-msg fb-err'; }
        if (btn) { btn.disabled = false; btn.textContent = 'Send feedback'; }
    }
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeFeedback(); });
