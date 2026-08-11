(function () {
    // ---- 状态 ----
    var state = {
        records: [],
        filter: '',
        selected: null,                                  // 选中的历史记录对象
        rename: { video_id: '', logic: '', name: '' },   // 待改名的文件
    };

    var $ = function (id) { return document.getElementById(id); };

    // 详情模式：URL ?video_id=xxx → 独立详情界面；否则列表模式
    var detailVideoId = new URLSearchParams(window.location.search).get('video_id');

    // ---- Toast ----
    var toastTimer = null;
    function toast(msg) {
        $('toastText').textContent = msg;
        $('toast').classList.add('show');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { $('toast').classList.remove('show'); }, 2200);
    }

    // ---- 工具 ----
    function fmtBytes(b) {
        if (b >= 1048576) return (b / 1048576).toFixed(1) + ' MB';
        if (b >= 1024) return (b / 1024).toFixed(0) + ' KB';
        return b + ' B';
    }
    function fmtTime(t) {
        var d = new Date(t * 1000);
        function pad(n) { return n < 10 ? '0' + n : '' + n; }
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
            + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    }
    function esc(s) {
        var div = document.createElement('div');
        div.textContent = s == null ? '' : String(s);
        return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // ---- 加载历史 ----
    function load() {
        return fetch('/api/history').then(function (r) { return r.json(); }).then(function (d) {
            state.records = d.records || [];
            if (detailVideoId) {
                renderDetailMode();
            } else {
                if (state.selected) {
                    state.selected = state.records.filter(function (r) { return r.video_id === state.selected.video_id; })[0] || null;
                }
                render();
            }
        }).catch(function () {
            toast('加载历史记录失败');
        });
    }

    // 详情模式：独立界面展示单条记录（隐藏列表/搜索，顶部返回列表）
    function renderDetailMode() {
        $('searchInput').classList.add('hidden');
        $('btnRefresh').classList.add('hidden');
        $('btnBack').classList.remove('hidden');
        $('historyList').classList.add('hidden');
        $('historyEmpty').classList.add('hidden');
        var rec = state.records.filter(function (r) { return r.video_id === detailVideoId; })[0];
        if (rec) {
            showDetail(rec);
        } else {
            toast('该记录不存在或已删除');
            window.location.href = '/history';  // 记录没了，回列表
        }
    }

    function filtered() {
        var q = state.filter.trim().toLowerCase();
        if (!q) return state.records;
        return state.records.filter(function (r) {
            return (r.title || '').toLowerCase().indexOf(q) !== -1
                || (r.video_id || '').indexOf(q) !== -1;
        });
    }

    // ---- 逻辑类型判断（用 logic 而非磁盘名，改名后仍能识别）----
    function hasLogic(rec, pred) {
        return rec.files.some(function (f) { return f.logic && pred(f.logic); });
    }

    // ---- 渲染列表 ----
    function render() {
        var list = filtered();
        $('historyEmpty').classList.toggle('hidden', list.length > 0);
        var el = $('historyList');
        el.innerHTML = '';

        list.forEach(function (r) {
            var card = document.createElement('div');
            card.className = 'history-item' + (state.selected && state.selected.video_id === r.video_id ? ' active' : '');
            var lang = r.language ? ' · ' + esc(r.language) : '';
            var tags = '';
            if (hasLogic(r, function (l) { return l === 'video.mp4'; })) tags += '<span class="pill">视频</span>';
            if (hasLogic(r, function (l) { return l === 'audio.mp3'; })) tags += '<span class="pill">音频</span>';
            if (hasLogic(r, function (l) { return l.indexOf('subtitles') === 0; })) tags += '<span class="pill">字幕</span>';
            if (hasLogic(r, function (l) { return l === 'transcript.md' || l.indexOf('transcript_') === 0; })) tags += '<span class="pill">文案</span>';

            card.innerHTML =
                '<div class="history-main">' +
                    '<div class="history-title">' + esc(r.title || ('视频 ' + r.video_id)) + '</div>' +
                    '<div class="history-meta">' + esc(r.video_id) + lang + ' · ' + fmtTime(r.time) + '</div>' +
                    '<div class="pills">' + tags + '</div>' +
                '</div>' +
                '<div class="history-actions">' +
                    '<button class="mini-btn gray" data-action="view" data-id="' + r.video_id + '">查看</button>' +
                    '<button class="mini-btn red danger" data-action="delete" data-id="' + r.video_id + '">删除</button>' +
                '</div>';
            el.appendChild(card);
        });

        el.onclick = function (e) {
            var btn = e.target.closest('[data-action]');
            if (!btn) return;
            var id = btn.getAttribute('data-id');
            var action = btn.getAttribute('data-action');
            var rec = state.records.filter(function (r) { return r.video_id === id; })[0];
            if (action === 'view') window.location.href = '/history?video_id=' + id;  // 跳独立详情界面
            else if (action === 'delete') confirmDelete(rec);
        };
    }

    // ---- 详情 ----
    function downloadUrl(rec, f) {
        if (f.logic === 'video.mp4') return '/api/download?video_id=' + rec.video_id + '&file=video.mp4';
        if (f.logic === 'audio.mp3') return '/api/download?video_id=' + rec.video_id + '&file=audio.mp3';
        if (f.logic === 'transcript.md' || f.logic.indexOf('transcript_') === 0) {
            var l = rec.language && rec.language !== 'auto' ? '&language=' + encodeURIComponent(rec.language) : '';
            return '/api/download?video_id=' + rec.video_id + '&file=transcript.md' + l;
        }
        if (f.logic === 'subtitles.srt' || f.logic.indexOf('subtitles_') === 0) {
            var l2 = rec.language && rec.language !== 'auto' ? '&language=' + encodeURIComponent(rec.language) : '';
            return '/api/download?video_id=' + rec.video_id + '&file=subtitles.srt' + l2;
        }
        return '';  // formatted 等无下载 API
    }

    function showDetail(rec) {
        state.selected = rec;
        if (!detailVideoId) render();  // 仅列表模式重绘高亮；详情独立界面无需
        $('detailTitle').textContent = rec.title || ('视频 ' + rec.video_id);
        $('detailMeta').textContent = rec.video_id + (rec.language ? ' · ' + rec.language : '') + ' · ' + fmtTime(rec.time);

        var media = '';
        if (hasLogic(rec, function (l) { return l === 'video.mp4'; })) {
            media += '<video class="media-player" controls playsinline preload="metadata" src="/api/media?video_id=' + rec.video_id + '&file=video.mp4"></video>';
        }
        if (hasLogic(rec, function (l) { return l === 'audio.mp3'; })) {
            media += '<audio class="media-player audio" controls preload="metadata" src="/api/media?video_id=' + rec.video_id + '&file=audio.mp3"></audio>';
        }
        $('detailMedia').innerHTML = media;

        var fl = '';
        rec.files.forEach(function (f) {
            var dl = downloadUrl(rec, f);
            var isText = f.logic && (f.logic.indexOf('transcript') === 0 || f.logic.indexOf('subtitles') === 0 || f.logic.indexOf('formatted') === 0);
            fl += '<div class="file-row">' +
                '<span class="file-name">' + esc(f.name) + '</span>' +
                '<span class="file-size muted">' + fmtBytes(f.size) + '</span>' +
                (isText ? '<button class="mini-btn gray" data-preview-logic="' + esc(f.logic) + '">查看</button>' : '') +
                (dl ? '<a class="mini-btn gray" href="' + dl + '" download>下载</a>' : '') +
                (f.logic ? '<button class="mini-btn gray" data-rename-logic="' + esc(f.logic) + '" data-rename-file="' + esc(f.name) + '">改名</button>' : '') +
                '</div>';
        });
        $('fileList').innerHTML = fl;

        $('fileList').onclick = function (e) {
            var pv = e.target.closest('[data-preview-logic]');
            if (pv) { openPreviewFile(rec, pv.getAttribute('data-preview-logic')); return; }
            var btn = e.target.closest('[data-rename-logic]');
            if (btn) openRename(rec, btn.getAttribute('data-rename-logic'), btn.getAttribute('data-rename-file'));
        };

        $('detailPanel').classList.remove('hidden');
        if (!detailVideoId) $('detailPanel').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    // ---- 轻量 Markdown 渲染（无外部依赖）：覆盖文案/排版的常见结构 ----
    // 安全：先 esc() 转义原始文本，再应用格式化；链接仅 http/https/# 协议。
    function inlineMd(s) {
        return s
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^*]+)\*/g, '<em>$1</em>')
            .replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (m, text, url) {
                url = url.trim();
                if (!/^(https?:|#)/i.test(url)) return m;  // 非 http/https/# 不渲染为链接
                return '<a href="' + url + '" target="_blank" rel="noopener">' + text + '</a>';
            });
    }
    function splitRow(row) {
        return row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(function (c) { return c.trim(); });
    }
    function renderTable(rows) {
        var html = '<table><thead><tr>';
        splitRow(rows[0]).forEach(function (c) { html += '<th>' + inlineMd(c) + '</th>'; });
        html += '</tr></thead><tbody>';
        for (var i = 2; i < rows.length; i++) {
            html += '<tr>';
            splitRow(rows[i]).forEach(function (c) { html += '<td>' + inlineMd(c) + '</td>'; });
            html += '</tr>';
        }
        return html + '</tbody></table>';
    }
    function renderMarkdown(src) {
        var lines = esc(src).split('\n');
        var out = [], i = 0;
        while (i < lines.length) {
            var trimmed = lines[i].trim();
            if (!trimmed) { i++; continue; }
            // 代码块
            if (trimmed.indexOf('```') === 0) {
                var buf = []; i++;
                while (i < lines.length && lines[i].trim().indexOf('```') !== 0) { buf.push(lines[i]); i++; }
                i++;  // 跳过结束 ```
                out.push('<pre><code>' + buf.join('\n') + '</code></pre>');
                continue;
            }
            // 标题
            var h = trimmed.match(/^(#{1,6})\s+(.*)$/);
            if (h) {
                var n = h[1].length;
                out.push('<h' + n + '>' + inlineMd(h[2]) + '</h' + n + '>'); i++;
                continue;
            }
            // 分隔线
            if (/^(-{3,}|\*{3,})$/.test(trimmed)) { out.push('<hr>'); i++; continue; }
            // 表格
            if (trimmed.indexOf('|') === 0 && i + 1 < lines.length && /^\|[\s:|-]+\|$/.test(lines[i + 1].trim())) {
                var rows = [];
                while (i < lines.length && lines[i].trim().indexOf('|') === 0) { rows.push(lines[i]); i++; }
                out.push(renderTable(rows));
                continue;
            }
            // 引用（esc 后 > 已被转义为 &gt;，两种开头都要识别）
            if (trimmed.indexOf('&gt;') === 0 || trimmed.indexOf('>') === 0) {
                var q = [];
                while (i < lines.length) {
                    var qt = lines[i].trim();
                    if (qt.indexOf('&gt;') !== 0 && qt.indexOf('>') !== 0) break;
                    q.push(qt.replace(/^&gt;\s?/, '').replace(/^>\s?/, ''));
                    i++;
                }
                out.push('<blockquote>' + q.map(inlineMd).join('<br>') + '</blockquote>');
                continue;
            }
            // 列表
            var listMatch = trimmed.match(/^([-*]|\d+[.)])\s+(.*)$/);
            if (listMatch) {
                var items = [], ordered = /\d/.test(listMatch[1]);
                while (i < lines.length) {
                    var m2 = lines[i].trim().match(/^([-*]|\d+[.)])\s+(.*)$/);
                    if (!m2) break;
                    items.push('<li>' + inlineMd(m2[2]) + '</li>'); i++;
                }
                out.push((ordered ? '<ol>' : '<ul>') + items.join('') + (ordered ? '</ol>' : '</ul>'));
                continue;
            }
            // 段落：收集连续非空、非已处理块级
            var para = [];
            while (i < lines.length) {
                var t = lines[i].trim();
                var stops = !t || t.indexOf('```') === 0 || /^#{1,6}\s/.test(t)
                    || /^(-{3,}|\*{3,})$/.test(t) || t.indexOf('|') === 0
                    || t.indexOf('&gt;') === 0 || t.indexOf('>') === 0 || /^([-*]|\d+[.)])\s/.test(t);
                if (stops) break;
                para.push(lines[i]); i++;
            }
            if (para.length) out.push('<p>' + para.map(inlineMd).join('<br>') + '</p>');
        }
        return out.join('\n');
    }

    // ---- 文本预览：md/srt 内容在网页内直接查看 ----
    function previewFileParam(logic) {
        if (logic.indexOf('transcript') === 0) return { file: 'transcript.md', lang: langFrom(logic) };
        if (logic.indexOf('subtitles') === 0) return { file: 'subtitles.srt', lang: langFrom(logic) };
        if (logic.indexOf('formatted') === 0) return { file: 'formatted.md', lang: langFrom(logic) };
        return null;
    }
    function langFrom(logic) {
        var m = logic.match(/^[a-z]+_(.+)\.(md|srt)$/);
        return m ? m[1] : '';
    }
    function openPreviewFile(rec, logic) {
        var p = previewFileParam(logic);
        if (!p) return;
        var url = '/api/content?video_id=' + rec.video_id + '&file=' + p.file + (p.lang ? '&language=' + encodeURIComponent(p.lang) : '');
        fetch(url).then(function (r) { return r.json(); }).then(function (d) {
            if (d.success) {
                $('previewTitle').textContent = d.name;
                if (/\.md$/i.test(logic)) {
                    // Markdown 预览：渲染为 HTML（先 esc 转义再格式化，防注入）
                    $('previewBody').classList.remove('plain-text');
                    $('previewBody').innerHTML = renderMarkdown(d.text);
                } else {
                    // srt 等纯文本：等宽展示，保留空白
                    $('previewBody').classList.add('plain-text');
                    $('previewBody').textContent = d.text;
                }
                $('previewModal').classList.remove('hidden');
            } else {
                toast(d.detail || '无法预览');
            }
        }).catch(function () { toast('网络错误，无法预览'); });
    }
    $('btnPreviewClose').addEventListener('click', function () { $('previewModal').classList.add('hidden'); });

    // ---- 改名 ----
    function openRename(rec, logic, file) {
        state.rename = { video_id: rec.video_id, logic: logic || '', name: file || '' };
        $('renameHint').textContent = '逻辑文件 ' + logic + ' → 输入新文件名（扩展名必须保留）';
        $('renameInput').value = file || '';
        $('renameModal').classList.remove('hidden');
        $('renameInput').focus();
        $('renameInput').select();
    }
    $('btnRenameCancel').addEventListener('click', function () { $('renameModal').classList.add('hidden'); });
    $('btnRenameOk').addEventListener('click', function () {
        var newName = $('renameInput').value.trim();
        if (!newName) { toast('文件名不能为空'); return; }
        var vid = state.rename.video_id, name = state.rename.name;
        fetch('/api/history/' + vid + '/rename', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name, newName: newName })
        }).then(function (r) { return r.json(); }).then(function (d) {
            $('renameModal').classList.add('hidden');
            if (d.success) {
                toast('已重命名');
                load().then(function () {
                    if (state.selected && state.selected.video_id === vid) showDetail(state.selected);
                });
            } else {
                toast(d.detail || '重命名失败');
            }
        }).catch(function () { toast('网络错误，重命名失败'); });
    });

    // ---- 删除（彻底删除 + 二次确认）----
    function confirmDelete(rec) {
        if (!window.confirm('确认删除「' + (rec.title || rec.video_id) + '」？\n其 视频/音频/文案/字幕 产物将被永久删除，且同链接重新提取需再次付费识别。')) return;
        fetch('/api/history/' + rec.video_id, { method: 'DELETE' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.success) {
                    if (detailVideoId) { toast('已删除'); window.location.href = '/history'; return; }  // 详情界面删除后回列表
                    if (state.selected && state.selected.video_id === rec.video_id) {
                        state.selected = null;
                        $('detailPanel').classList.add('hidden');
                    }
                    toast('已删除');
                    load();
                } else {
                    toast(d.detail || '删除失败');
                }
            })
            .catch(function () { toast('网络错误，删除失败'); });
    }

    // ---- 一键打包：把这条记录的全部产物压缩下载 ----
    function downloadZipAll() {
        if (!state.selected) return;
        var btn = $('btnZipAll');
        if (btn.disabled) return;
        btn.disabled = true;
        btn.textContent = '打包中…';
        setTimeout(function () {  // 浏览器下载无回调，定时兜底恢复按钮
            btn.disabled = false;
            btn.textContent = '⬇ 打包全部';
        }, 8000);
        window.location.href = '/api/history/' + state.selected.video_id + '/download';
    }
    $('btnZipAll').addEventListener('click', downloadZipAll);

    // ---- 事件绑定 ----
    $('searchInput').addEventListener('input', function () {
        state.filter = this.value;
        render();
    });
    $('btnRefresh').addEventListener('click', load);
    $('btnCloseDetail').addEventListener('click', function () {
        if (detailVideoId) { window.location.href = '/history'; return; }  // 详情界面：返回列表
        state.selected = null;
        $('detailPanel').classList.add('hidden');
        render();
    });

    load();
})();
