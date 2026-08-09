(function () {
    // ---- 状态 ----
    var state = {
        url: '',
        language: 'auto',   // 识别语言：auto/zh-CN/en-US
        loading: false,
        apiKeyConfigured: false,
        deepseekConfigured: false,
        videoInfo: null,
        transcript: '',
        formattedText: '',    // AI 排版结果
        currentView: 'original', // 'original' | 'formatted'
        step: 0,
        lastAction: '',       // 最近一次操作：'info' | 'extract'
        steps: ['解析分享链接', '下载视频', '提取音频', '豆包识别文案'],
    };

    var $ = function (id) { return document.getElementById(id); };
    var urlInput = $('urlInput'), btnInfo = $('btnInfo'), btnExtract = $('btnExtract');

    // ---- Toast ----
    var toastTimer = null;
    function toast(msg) {
        $('toastText').textContent = msg;
        $('toast').classList.add('show');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { $('toast').classList.remove('show'); }, 2000);
    }

    // ---- 健康检查 ----
    function checkHealth() {
        fetch('/api/health').then(function (r) { return r.json(); }).then(function (d) {
            state.apiKeyConfigured = !!d.api_key_configured;
            state.deepseekConfigured = !!d.deepseek_configured;
            var b = $('apiBadge');
            if (state.apiKeyConfigured) {
                b.className = 'status-badge on';
                $('apiBadgeText').textContent = 'API 已连接';
            } else {
                b.className = 'status-badge off';
                $('apiBadgeText').textContent = 'API 未配置';
                $('apiNotice').classList.remove('hidden');
            }
        }).catch(function () {
            $('apiBadgeText').textContent = '服务异常';
        });
    }

    // ---- 输入联动 ----
    function syncInput() {
        state.url = urlInput.value.trim();
        btnInfo.disabled = !state.url || state.loading;
        btnExtract.disabled = !state.url || state.loading || !state.apiKeyConfigured;
    }
    urlInput.addEventListener('input', syncInput);
    urlInput.addEventListener('keydown', function (e) {
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); extractText(); }
    });

    // ---- 通用请求 ----
    function postJSON(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).then(function (r) { return r.json(); });
    }

    function showLoading(text) {
        $('loadingText').textContent = text;
        $('loadingCard').classList.remove('hidden');
        $('errorCard').classList.add('hidden');
    }
    function hideLoading() { $('loadingCard').classList.add('hidden'); }

    // 展示友好错误：code 用于补充提示（如未开通资源的控制台链接）
    function showError(msg, code) {
        $('errorText').textContent = msg;
        $('errorCard').classList.remove('hidden');
        if (code) {
            $('errorHint').classList.remove('hidden');
            $('errorCodeText').textContent = '错误码: ' + code;
            // 未开通录音文件识别时，给出控制台直达链接
            $('errorHelpLink').style.display = (code === '45000030') ? 'inline-flex' : 'none';
        } else {
            $('errorHint').classList.add('hidden');
        }
    }

    function resetState() {
        $('errorCard').classList.add('hidden');
        $('infoCard').classList.add('hidden');
        $('resultCard').classList.add('hidden');
        $('emptyState').classList.remove('hidden');
        // 清空媒体播放器
        $('mediaVideo').classList.add('hidden');
        $('mediaAudio').classList.add('hidden');
        $('videoPlayer').removeAttribute('src');
        $('audioPlayer').removeAttribute('src');
    }

    // ---- 获取信息 ----
    function extractInfo() {
        if (!state.url || state.loading) return;
        resetState();
        state.lastAction = 'info';
        state.loading = true;
        syncInput();
        showLoading('正在获取视频信息...');
        postJSON('/api/video/info', { url: state.url }).then(function (d) {
            hideLoading();
            if (d.success) {
                state.videoInfo = d;
                $('infoId').textContent = d.video_id;
                $('infoId2').textContent = d.video_id;
                $('infoTitle').textContent = d.title;
                $('infoCard').classList.remove('hidden');
                toast('视频信息获取成功');
            } else {
                showError(d.error || '获取视频信息失败', d.code);
            }
        }).catch(function () {
            hideLoading();
            showError('网络错误，请检查服务是否运行');
        }).finally(function () { state.loading = false; syncInput(); });
    }

    // ---- 提取文案 ----
    // ---- 渐进式展示：视频/音频一就绪就展示，不等 ASR ----

    // 视频下载完成即展示
    function showVideo(d) {
        var id = d.video_id;
        state.videoInfo = state.videoInfo || {};
        state.videoInfo.video_id = id;
        if (d.title) state.videoInfo.title = d.title;
        $('videoPlayer').src = '/api/media?video_id=' + id + '&file=video.mp4';
        $('dlVideo').href = '/api/download?video_id=' + id + '&file=video.mp4';
        $('mediaVideo').classList.remove('hidden');
    }

    // 音频提取完成即展示
    function showAudio(d) {
        var id = d.video_id;
        $('audioPlayer').src = '/api/media?video_id=' + id + '&file=audio.mp3';
        $('dlAudio').href = '/api/download?video_id=' + id + '&file=audio.mp3';
        $('mediaAudio').classList.remove('hidden');
    }

    // 识别完成：展示文案与下载入口
    function finishExtract(d) {
        state.videoInfo = { video_id: d.video_id, title: d.title };
        state.transcript = d.text;
        state.formattedText = '';
        state.currentView = 'original';
        $('resultTitle').textContent = d.title;
        $('dlText').href = '/api/download?video_id=' + d.video_id + '&file=transcript.md&language=' + encodeURIComponent(state.language);
        // 字幕与文案同源：识别带时间轴才有字幕可下载
        if (d.has_subtitles) {
            $('dlSrt').classList.remove('hidden');
            $('dlSrt').href = '/api/download?video_id=' + d.video_id + '&file=subtitles.srt&language=' + encodeURIComponent(state.language);
        } else {
            $('dlSrt').classList.add('hidden');
        }
        $('emptyState').classList.add('hidden');
        $('resultCard').classList.remove('hidden');
        setTab();
        if (d.from_cache) {
            $('cacheBadge').classList.remove('hidden');
            toast('⚡ 命中历史记录，直接返回');
        } else {
            $('cacheBadge').classList.add('hidden');
            toast('文案提取成功');
        }
    }

    // 切换 原始/AI排版 视图
    function setTab() {
        var isFormatted = state.currentView === 'formatted';
        $('tabOriginal').classList.toggle('active', !isFormatted);
        $('tabFormatted').classList.toggle('active', isFormatted);
        var text = isFormatted ? state.formattedText : state.transcript;
        $('transcriptText').textContent = text || '';
        $('charCount').textContent = (text || '').length + ' 字';
    }

    // AI 排版：调用 DeepSeek 整理文案
    function formatText() {
        if (!state.transcript) return;
        if (!state.deepseekConfigured) {
            toast('未配置 DEEPSEEK_API_KEY，请在 .env 中填写后重启');
            return;
        }
        $('tabFormatted').disabled = true;
        $('formatStatus').textContent = 'AI 排版中...';
        fetch('/api/format', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: state.transcript })
        }).then(function (r) { return r.json(); }).then(function (d) {
            if (d.success) {
                state.formattedText = d.text;
                state.currentView = 'formatted';
                setTab();
                toast('✨ 排版完成');
            } else {
                toast(d.error || '排版失败');
            }
        }).catch(function () {
            toast('网络错误，排版失败');
        }).finally(function () {
            $('tabFormatted').disabled = false;
            $('formatStatus').textContent = '';
        });
    }

    // 解析 SSE 事件并驱动界面
    function handleSSE(rawEvent) {
        var lines = rawEvent.split('\n');
        for (var i = 0; i < lines.length; i++) {
            if (lines[i].indexOf('data: ') !== 0) continue;
            var d;
            try { d = JSON.parse(lines[i].slice(6)); } catch (e) { continue; }
            switch (d.stage) {
                case 'parse':
                    state.step = 1; renderSteps();
                    $('loadingText').textContent = '正在解析分享链接...';
                    break;
                case 'video_ready':
                    showVideo(d);
                    state.step = 2; renderSteps();
                    $('loadingText').textContent = '视频已就绪，正在提取音频...';
                    break;
                case 'audio_ready':
                    showAudio(d);
                    state.step = 4; renderSteps();
                    $('loadingText').textContent = '音频已就绪，豆包识别中（约需几十秒）...';
                    break;
                case 'done':
                    finishExtract(d);
                    state.step = state.steps.length + 1; renderSteps();
                    hideLoading();
                    break;
                case 'error':
                    hideLoading();
                    showError(d.message || '提取失败', d.code);
                    break;
            }
        }
    }

    function extractText() {
        if (!state.url || state.loading) return;
        if (!state.apiKeyConfigured) { toast('请先配置 API Key'); return; }

        resetState();
        state.lastAction = 'extract';
        state.loading = true;
        syncInput();
        showLoading('正在解析分享链接...');
        state.step = 0;
        renderSteps();

        fetch('/api/extract/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: state.url, language: state.language })
        }).then(function (res) {
            if (!res.ok || !res.body) throw new Error('HTTP ' + res.status);
            var reader = res.body.getReader();
            var decoder = new TextDecoder();
            var buffer = '';
            function pump() {
                return reader.read().then(function (r) {
                    if (r.done) return;
                    buffer += decoder.decode(r.value, { stream: true });
                    var idx;
                    while ((idx = buffer.indexOf('\n\n')) !== -1) {
                        handleSSE(buffer.slice(0, idx));
                        buffer = buffer.slice(idx + 2);
                    }
                    return pump();
                });
            }
            return pump();
        }).catch(function () {
            hideLoading();
            showError('网络错误，请检查服务是否运行');
        }).finally(function () {
            state.loading = false;
            syncInput();
        });
    }

    function renderSteps() {
        var el = $('steps');
        el.innerHTML = '';
        state.steps.forEach(function (name, i) {
            var n = i + 1;
            var div = document.createElement('div');
            div.className = 'step-item' + (state.step > n ? ' done' : state.step === n ? ' active' : '');
            var num = document.createElement('div');
            num.className = 'step-num';
            num.textContent = state.step > n ? '✓' : n;
            var span = document.createElement('span');
            span.textContent = name;
            div.appendChild(num); div.appendChild(span);
            el.appendChild(div);
        });
    }

    // ---- 复制（当前视图）----
    $('btnCopy').addEventListener('click', function () {
        var text = state.currentView === 'formatted' ? state.formattedText : state.transcript;
        if (!text) return;
        navigator.clipboard.writeText(text).then(function () {
            toast('已复制到剪贴板');
        }).catch(function () { toast('复制失败，请手动选择'); });
    });

    // ---- AI 排版视图切换 ----
    $('tabOriginal').addEventListener('click', function () {
        state.currentView = 'original';
        setTab();
    });
    $('tabFormatted').addEventListener('click', function () {
        if (state.formattedText) {
            state.currentView = 'formatted';
            setTab();
        } else {
            formatText();
        }
    });

    btnInfo.addEventListener('click', extractInfo);
    btnExtract.addEventListener('click', extractText);
    $('btnRetry').addEventListener('click', function () {
        if (state.lastAction === 'info') { extractInfo(); } else { extractText(); }
    });

    // 粘贴按钮：读取剪贴板并填入输入框
    $('btnPaste').addEventListener('click', function () {
        navigator.clipboard.readText().then(function (text) {
            if (!text) { toast('剪贴板为空'); return; }
            urlInput.value = text;
            syncInput();
            toast('已粘贴剪贴板内容');
        }).catch(function () {
            toast('无法读取剪贴板，请手动粘贴');
        });
    });

    // 清空按钮：清空输入框并重置界面
    $('btnClear').addEventListener('click', function () {
        urlInput.value = '';
        resetState();
        syncInput();
        toast('已清空');
    });

    // ---- 识别语言下拉（自定义，保证与 chip-btn 完全对齐）----
    var LANG_LABELS = { auto: '默认识别', 'zh-CN': '中文', 'en-US': '英文' };
    function closeLangMenu() {
        $('langMenu').classList.add('hidden');
        $('langBtn').setAttribute('aria-expanded', 'false');
    }
    function setLanguage(v) {
        state.language = v;
        $('langLabel').textContent = LANG_LABELS[v] || v;
        var items = document.querySelectorAll('#langMenu .dropdown-item');
        for (var i = 0; i < items.length; i++) {
            items[i].classList.toggle('active', items[i].getAttribute('data-lang') === v);
        }
        closeLangMenu();
    }
    $('langBtn').addEventListener('click', function (e) {
        e.stopPropagation();
        var open = $('langMenu').classList.toggle('hidden') === false;
        $('langBtn').setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    var langItems = document.querySelectorAll('#langMenu .dropdown-item');
    for (var j = 0; j < langItems.length; j++) {
        langItems[j].addEventListener('click', function () {
            setLanguage(this.getAttribute('data-lang'));
        });
    }
    document.addEventListener('click', closeLangMenu);

    checkHealth();
    syncInput();
})();