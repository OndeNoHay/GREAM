/**
 * GraphRagExec - Frontend Application
 */

// State
let currentLibraryId = null;
let libraries = [];
let chatHistory = [];  // [{role, content, sourcesHtml, entitiesHtml, metaHtml}]

// Markdown rendering
let markdownRenderEnabled = localStorage.getItem('markdownRenderEnabled') !== 'false';

// Configure marked for GitHub Flavored Markdown (tables, strikethrough, etc.)
if (typeof marked !== 'undefined') {
    marked.setOptions({ gfm: true, breaks: true });
}

function renderMessageContent(rawText) {
    if (markdownRenderEnabled && typeof marked !== 'undefined') {
        return marked.parse(rawText);
    }
    return `<pre class="md-raw">${escapeHtml(rawText)}</pre>`;
}

// Escape a string for use as an HTML attribute value
function attrEscape(str) {
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}

function onMarkdownToggleChange() {
    markdownRenderEnabled = document.getElementById('chat-render-markdown').checked;
    localStorage.setItem('markdownRenderEnabled', markdownRenderEnabled);
    // Re-render all existing assistant message contents
    document.querySelectorAll('.message-content[data-raw]').forEach(el => {
        el.innerHTML = renderMessageContent(el.dataset.raw);
    });
}

// =============================================================================
// Chat History (localStorage persistence)
// =============================================================================

function saveChatHistory() {
    if (!currentLibraryId) return;
    try {
        const toSave = chatHistory.slice(-50);  // cap at 50 messages
        localStorage.setItem('chat_history_' + currentLibraryId, JSON.stringify(toSave));
    } catch (e) { /* quota exceeded — ignore */ }
}

function loadChatHistory(libraryId) {
    chatHistory = [];
    const container = document.getElementById('chat-messages');
    if (!container) return;

    // Always reset the container first so previous library's messages don't bleed through
    const welcomeHtml = `<div class="chat-welcome">
        <h3>Ask a Question</h3>
        <p>Ask questions about your documents and get AI-generated answers with citations.</p>
    </div>`;

    let stored;
    try {
        stored = localStorage.getItem('chat_history_' + libraryId);
    } catch (e) {
        container.innerHTML = welcomeHtml;
        return;
    }

    if (!stored) {
        container.innerHTML = welcomeHtml;
        return;
    }

    try {
        chatHistory = JSON.parse(stored);
    } catch (e) {
        chatHistory = [];
        container.innerHTML = welcomeHtml;
        return;
    }

    if (chatHistory.length === 0) {
        container.innerHTML = welcomeHtml;
        return;
    }

    container.innerHTML = '';
    for (const msg of chatHistory) {
        container.innerHTML += buildHistoryMessageHtml(msg);
    }
    container.scrollTop = container.scrollHeight;
}

function buildHistoryMessageHtml(msg) {
    if (msg.role === 'user') {
        return `<div class="chat-message user">
            <div class="message-header">You ${copyBtnHtml()}</div>
            <div class="message-content">${escapeHtml(msg.content)}</div>
        </div>`;
    }
    if (msg.role === 'error') {
        return `<div class="chat-message assistant">
            <div class="message-header">Error ${copyBtnHtml()}</div>
            <div class="message-content">${escapeHtml(msg.content)}</div>
        </div>`;
    }
    // assistant
    const headerName = msg.agentName || 'Assistant';
    const extraClass = msg.agentName ? ' agent-final' : '';
    return `<div class="chat-message assistant${extraClass}">
        <div class="message-header">${escapeHtml(headerName)} ${copyBtnHtml()}</div>
        <div class="message-content" data-raw="${attrEscape(msg.content)}">${renderMessageContent(msg.content)}</div>
        ${msg.sourcesHtml || ''}
        ${msg.entitiesHtml || ''}
        ${msg.metaHtml || ''}
    </div>`;
}

// API helpers
async function api(endpoint, options = {}) {
    const response = await fetch(endpoint, {
        headers: {
            'Content-Type': 'application/json',
            ...options.headers,
        },
        ...options,
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Request failed' }));
        throw new Error(error.detail || 'Request failed');
    }

    return response.json();
}

// Toast notifications
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 3000);
}

// Modal helpers
function openModal(id) {
    document.getElementById(id).classList.remove('hidden');
}

function closeModal(id) {
    document.getElementById(id).classList.add('hidden');
}

// Libraries
async function loadLibraries() {
    try {
        const data = await api('/api/libraries');
        libraries = data.libraries;
        renderLibraries();

        // Select first library if none selected
        if (!currentLibraryId && libraries.length > 0) {
            selectLibrary(libraries[0].id);
        }
    } catch (error) {
        showToast('Failed to load libraries: ' + error.message, 'error');
    }
}

function renderLibraries() {
    const list = document.getElementById('library-list');
    list.innerHTML = libraries.map(lib => `
        <li class="library-item ${lib.id === currentLibraryId ? 'active' : ''}"
            data-id="${lib.id}" onclick="selectLibrary('${lib.id}')">
            <span class="name">${escapeHtml(lib.name)}</span>
            <span class="count">${lib.document_count}</span>
        </li>
    `).join('');
}

function selectLibrary(id) {
    currentLibraryId = id;
    renderLibraries();
    loadSources();
    loadChatHistory(id);
    // Clear search results
    document.getElementById('search-results').innerHTML =
        '<p class="placeholder-text">Enter a query to search your documents</p>';
}

async function createLibrary() {
    const name = document.getElementById('library-name').value.trim();
    const description = document.getElementById('library-description').value.trim();

    if (!name) {
        showToast('Library name is required', 'error');
        return;
    }

    try {
        await api('/api/libraries', {
            method: 'POST',
            body: JSON.stringify({ name, description }),
        });

        closeModal('library-modal');
        showToast('Library created successfully', 'success');
        loadLibraries();
    } catch (error) {
        showToast('Failed to create library: ' + error.message, 'error');
    }
}

// Sources
async function loadSources() {
    if (!currentLibraryId) return;

    try {
        const sources = await api(`/api/documents/sources/${currentLibraryId}`);
        renderSources(sources);
    } catch (error) {
        console.error('Failed to load sources:', error);
    }
}

function renderSources(sources) {
    const list = document.getElementById('sources-list');

    if (sources.length === 0) {
        list.innerHTML = '<li class="source-item"><span class="text-muted">No documents imported yet</span></li>';
        return;
    }

    list.innerHTML = sources.map(source => `
        <li class="source-item">
            <span class="source-name clickable" onclick="showSourceDetails('${escapeHtml(source)}')">${escapeHtml(source)}</span>
            <div class="source-actions">
                <button class="btn btn-secondary btn-sm" onclick="openSource('${escapeHtml(source)}')" title="Open with system default app">Open</button>
                <button class="btn btn-danger btn-sm" onclick="deleteSource('${escapeHtml(source)}')">Delete</button>
            </div>
        </li>
    `).join('');
}

// Source Details
async function showSourceDetails(sourceName) {
    if (!currentLibraryId) {
        showToast('Please select a library first', 'warning');
        return;
    }

    // Open modal and show loading
    openModal('source-details-modal');
    document.getElementById('source-details-title').textContent = sourceName;
    document.getElementById('source-details-loading').classList.remove('hidden');
    document.getElementById('source-details-content').classList.add('hidden');

    try {
        const details = await api(`/api/documents/source/${currentLibraryId}/${encodeURIComponent(sourceName)}/details`);

        // Hide loading, show content
        document.getElementById('source-details-loading').classList.add('hidden');
        document.getElementById('source-details-content').classList.remove('hidden');

        // Update summary stats
        document.getElementById('detail-chunk-count').textContent = details.chunk_count || 0;
        document.getElementById('detail-entity-count').textContent = details.entity_count || 0;

        // Render chunks
        const chunksList = document.getElementById('detail-chunks-list');
        if (details.chunks && details.chunks.length > 0) {
            chunksList.innerHTML = details.chunks.map(chunk => `
                <div class="detail-item">
                    <div class="chunk-header">
                        <span>Chunk ${chunk.chunk_index}</span>
                        <span class="chunk-meta">${chunk.page ? `Page ${chunk.page}` : 'No page'}</span>
                    </div>
                    <div class="chunk-meta">ID: ${escapeHtml(chunk.chunk_id)}</div>
                    <div class="chunk-meta">Embedding: ${chunk.embedding_dim} dimensions</div>
                    ${chunk.embedding_preview && chunk.embedding_preview.length > 0 ?
                        `<div class="embedding-preview">[${chunk.embedding_preview.map(v => v.toFixed(4)).join(', ')}...]</div>` : ''}
                </div>
            `).join('');
        } else {
            chunksList.innerHTML = '<div class="detail-item text-muted">No chunks found</div>';
        }

        // Render entities
        const entitiesList = document.getElementById('detail-entities-list');
        if (details.entities && details.entities.length > 0) {
            entitiesList.innerHTML = details.entities.map(entity => `
                <span class="entity-item">
                    ${escapeHtml(entity.name)}
                    <span class="entity-type">${escapeHtml(entity.type)}</span>
                </span>
            `).join('');
        } else {
            entitiesList.innerHTML = '<div class="detail-item text-muted">No entities extracted</div>';
        }

    } catch (error) {
        document.getElementById('source-details-loading').classList.add('hidden');
        document.getElementById('source-details-content').classList.remove('hidden');
        document.getElementById('detail-chunks-list').innerHTML = `<div class="detail-item text-muted">Error: ${escapeHtml(error.message)}</div>`;
        document.getElementById('detail-entities-list').innerHTML = '';
        showToast('Failed to load source details: ' + error.message, 'error');
    }
}

async function deleteSource(sourceName) {
    if (!confirm(`Delete all data from "${sourceName}"?`)) return;

    try {
        await api(`/api/documents/source/${currentLibraryId}/${encodeURIComponent(sourceName)}`, {
            method: 'DELETE',
        });

        showToast('Source deleted successfully', 'success');
        loadSources();
        loadLibraries();
    } catch (error) {
        showToast('Failed to delete source: ' + error.message, 'error');
    }
}

async function openSource(sourceName) {
    try {
        await api(`/api/documents/open/${currentLibraryId}/${encodeURIComponent(sourceName)}`, {
            method: 'POST',
        });
    } catch (error) {
        // Fallback: serve the file through the browser
        window.open(`/api/documents/file/${currentLibraryId}/${encodeURIComponent(sourceName)}`, '_blank');
    }
}

// Library management
async function clearLibraryVectors() {
    if (!currentLibraryId) {
        showToast('Please select a library first', 'warning');
        return;
    }

    const lib = libraries.find(l => l.id === currentLibraryId);
    const libName = lib ? lib.name : currentLibraryId;

    if (!confirm(`Delete ALL vector embeddings from "${libName}"?\n\nThis will remove all searchable content. You will need to re-import documents to search again.`)) return;

    try {
        const result = await api(`/api/documents/vectors/${currentLibraryId}`, {
            method: 'DELETE',
        });

        showToast(result.message, 'success');
        loadSources();
        loadLibraries();
    } catch (error) {
        showToast('Failed to clear vectors: ' + error.message, 'error');
    }
}

async function clearLibraryGraphs() {
    if (!currentLibraryId) {
        showToast('Please select a library first', 'warning');
        return;
    }

    const lib = libraries.find(l => l.id === currentLibraryId);
    const libName = lib ? lib.name : currentLibraryId;

    if (!confirm(`Delete ALL graph data (entities and relationships) from "${libName}"?\n\nGraph search will return no results until documents are re-imported.`)) return;

    try {
        const result = await api(`/api/documents/graphs/${currentLibraryId}`, {
            method: 'DELETE',
        });

        showToast(result.message, 'success');
        loadSources();
        loadLibraries();
    } catch (error) {
        showToast('Failed to clear graphs: ' + error.message, 'error');
    }
}

async function deleteCurrentLibrary() {
    if (!currentLibraryId) {
        showToast('Please select a library first', 'warning');
        return;
    }

    const lib = libraries.find(l => l.id === currentLibraryId);
    const libName = lib ? lib.name : currentLibraryId;

    if (!confirm(`DELETE the entire library "${libName}"?\n\nThis will permanently remove ALL documents, vectors, and graph data. This action cannot be undone.`)) return;

    try {
        const result = await api(`/api/libraries/${currentLibraryId}`, {
            method: 'DELETE',
        });

        showToast(result.message, 'success');
        try { localStorage.removeItem('chat_history_' + currentLibraryId); } catch (e) { /* ignore */ }
        currentLibraryId = null;
        chatHistory = [];
        await loadLibraries();

        // Select first remaining library
        if (libraries.length > 0) {
            selectLibrary(libraries[0].id);
        }
    } catch (error) {
        showToast('Failed to delete library: ' + error.message, 'error');
    }
}

// Chat
async function sendChatMessage() {
    const input = document.getElementById('chat-input');
    const query = input.value.trim();

    if (!query) {
        showToast('Please enter a question', 'warning');
        return;
    }

    if (!currentLibraryId) {
        showToast('Please select a library first', 'warning');
        return;
    }

    // Check if an agent is selected
    const agentSelect = document.getElementById('chat-agent-select');
    const selectedAgentId = agentSelect ? agentSelect.value : '';

    if (selectedAgentId) {
        sendAgentChatMessage(query, selectedAgentId);
        return;
    }

    const messagesContainer = document.getElementById('chat-messages');

    // Clear welcome message if present
    const welcome = messagesContainer.querySelector('.chat-welcome');
    if (welcome) welcome.remove();

    // Add user message
    messagesContainer.innerHTML += `
        <div class="chat-message user">
            <div class="message-header">You ${copyBtnHtml()}</div>
            <div class="message-content">${escapeHtml(query)}</div>
        </div>
    `;
    chatHistory.push({ role: 'user', content: query });
    saveChatHistory();

    // Add loading indicator
    messagesContainer.innerHTML += `
        <div class="chat-loading" id="chat-loading">
            <div class="spinner"></div>
            <span>Thinking...</span>
        </div>
    `;

    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    input.value = '';

    try {
        // Send the last saved turns (before the current user message) as conversation history.
        // The backend enforces the max_conversation_history cap, so we just send what we have.
        // chatHistory already includes the user message we just pushed, so exclude the last entry.
        const historyToSend = chatHistory.slice(0, -1)
            .filter(m => m.role === 'user' || m.role === 'assistant')
            .map(m => ({ role: m.role, content: m.content }));

        const data = await api('/api/search/chat', {
            method: 'POST',
            body: JSON.stringify({
                query,
                library_id: currentLibraryId,
                use_vector_search: document.getElementById('chat-use-vector').checked,
                use_graph_search: document.getElementById('chat-use-graph').checked,
                top_k: Math.max(1, parseInt(document.getElementById('chat-top-k').value) || 5),
                conversation_history: historyToSend,
            }),
        });

        // Remove loading indicator
        const loading = document.getElementById('chat-loading');
        if (loading) loading.remove();

        // Build sources HTML
        const sourcesHtml = data.sources.length > 0
            ? `<div class="message-sources">
                <strong>Sources:</strong>
                ${data.sources.map(s => {
                    const label = escapeHtml(s.source_file) + (s.page ? ` p.${s.page}` : '');
                    return s.file_link
                        ? `<a class="source-tag source-tag-link" href="${escapeHtml(s.file_link)}" target="_blank" rel="noopener noreferrer">${label}</a>`
                        : `<span class="source-tag">${label}</span>`;
                }).join('')}
               </div>`
            : '';

        // Build graph entities HTML
        const entitiesHtml = buildGraphEntitiesHtml(data.graph_entities || [], data.library_id);

        // Build metadata HTML showing vector/graph counts
        const metaHtml = `
            <div class="message-meta">
                <span class="meta-item"><span class="vector-dot"></span> ${data.vector_results_count || 0} vector</span>
                <span class="meta-item"><span class="graph-dot"></span> ${data.graph_results_count || 0} graph</span>
            </div>
        `;

        messagesContainer.innerHTML += `
            <div class="chat-message assistant">
                <div class="message-header">Assistant ${copyBtnHtml()}</div>
                <div class="message-content" data-raw="${attrEscape(data.answer)}">${renderMessageContent(data.answer)}</div>
                ${sourcesHtml}
                ${entitiesHtml}
                ${metaHtml}
            </div>
        `;
        chatHistory.push({ role: 'assistant', content: data.answer, sourcesHtml, entitiesHtml, metaHtml });
        saveChatHistory();

        messagesContainer.scrollTop = messagesContainer.scrollHeight;

    } catch (error) {
        // Remove loading indicator
        const loading = document.getElementById('chat-loading');
        if (loading) loading.remove();

        messagesContainer.innerHTML += `
            <div class="chat-message assistant">
                <div class="message-header">Error ${copyBtnHtml()}</div>
                <div class="message-content">Failed to get response: ${escapeHtml(error.message)}</div>
            </div>
        `;
        chatHistory.push({ role: 'error', content: `Failed to get response: ${error.message}` });
        saveChatHistory();
    }
}

// Agent-powered chat
let currentChatAgentTaskId = null;

async function sendAgentChatMessage(query, agentId) {
    const messagesContainer = document.getElementById('chat-messages');
    const input = document.getElementById('chat-input');
    const sendBtn = document.getElementById('btn-chat');

    // Clear welcome message if present
    const welcome = messagesContainer.querySelector('.chat-welcome');
    if (welcome) welcome.remove();

    // Find agent name for display
    const agentName = agents.find(a => a.id === agentId)?.name || 'Agent';

    // Add user message and record to history
    messagesContainer.innerHTML += `
        <div class="chat-message user">
            <div class="message-header">You ${copyBtnHtml()}</div>
            <div class="message-content">${escapeHtml(query)}</div>
        </div>
    `;
    chatHistory.push({ role: 'user', content: query });
    saveChatHistory();

    // Show standard loading spinner immediately (same as non-agent)
    const loadingId = 'agent-chat-loading-' + Date.now();
    messagesContainer.innerHTML += `
        <div class="chat-loading" id="${loadingId}">
            <div class="spinner"></div>
            <span>${escapeHtml(agentName)}...</span>
        </div>
    `;

    // Add collapsible analysis block (hidden until first event arrives)
    const thinkingId = 'agent-chat-thinking-' + Date.now();
    messagesContainer.innerHTML += `
        <div class="chat-message agent-thinking hidden" id="${thinkingId}">
            <div class="message-header">${escapeHtml(agentName)}</div>
            <div class="message-content">
                <details class="agent-analysis-details">
                    <summary class="agent-analysis-summary">
                        <div class="agent-thinking-indicator">
                            <div class="spinner" id="${thinkingId}-spinner"></div>
                            <span id="${thinkingId}-status">Starting analysis...</span>
                        </div>
                    </summary>
                    <div id="${thinkingId}-log" class="agent-analysis-log"></div>
                </details>
            </div>
        </div>
    `;

    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    input.value = '';
    sendBtn.disabled = true;

    try {
        const response = await fetch('/api/search/chat/agent', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                query,
                library_id: currentLibraryId,
                agent_id: agentId,
            }),
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Failed to start agent' }));
            throw new Error(error.detail);
        }

        currentChatAgentTaskId = response.headers.get('X-Task-ID');

        // Process SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const event = JSON.parse(line.slice(6));
                        handleAgentChatEvent(event, messagesContainer, thinkingId, agentName, loadingId);
                    } catch (e) {
                        console.warn('Failed to parse agent chat event:', e);
                    }
                }
            }
        }

    } catch (error) {
        // Remove thinking indicator
        const thinking = document.getElementById(thinkingId);
        if (thinking) thinking.remove();

        messagesContainer.innerHTML += `
            <div class="chat-message agent-error">
                <div class="message-header">Error</div>
                <div class="message-content">${escapeHtml(error.message)}</div>
            </div>
        `;
    } finally {
        sendBtn.disabled = false;
        currentChatAgentTaskId = null;
        const loadingEl = document.getElementById(loadingId);
        if (loadingEl) loadingEl.remove();
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
}

function handleAgentChatEvent(event, container, thinkingId, agentName, loadingId) {
    const thinkingEl = document.getElementById(thinkingId);
    const statusEl = document.getElementById(thinkingId + '-status');
    const logEl = document.getElementById(thinkingId + '-log');

    const updateStatus = (text) => { if (statusEl) statusEl.textContent = text; };
    const addToLog = (html) => {
        if (logEl) { logEl.innerHTML += html; }
    };

    // On first event: swap loading spinner for the thinking block
    const revealThinking = () => {
        const loadingEl = document.getElementById(loadingId);
        if (loadingEl) loadingEl.remove();
        if (thinkingEl) thinkingEl.classList.remove('hidden');
    };

    switch (event.type) {
        case 'started':
            revealThinking();
            updateStatus('Analyzing your question...');
            break;

        case 'thinking':
            revealThinking();
            updateStatus(event.content || 'Thinking...');
            break;

        case 'tool_call': {
            revealThinking();
            const toolLabel = event.tool.replace(/_/g, ' ');
            const argsPreview = Object.entries(event.args || {})
                .map(([k, v]) => `${k}: ${typeof v === 'string' && v.length > 60 ? v.slice(0, 60) + '…' : v}`)
                .join(', ');
            addToLog(`
                <div class="agent-log-entry log-tool-call">
                    <span class="log-tool-name">${escapeHtml(toolLabel)}</span>
                    <span class="log-args">${escapeHtml(argsPreview)}</span>
                </div>
            `);
            container.scrollTop = container.scrollHeight;
            break;
        }

        case 'approval_needed': {
            const approval = event.approval;
            const autoApprove = document.getElementById('chat-auto-approve')?.checked;

            if (autoApprove) {
                addToLog(`
                    <div class="agent-log-entry log-tool-call">
                        <span class="log-tool-name">${escapeHtml(approval.tool.replace(/_/g, ' '))}</span>
                        <span class="approval-resolved approved" style="margin-left:0.5rem">Auto-approved</span>
                    </div>
                `);
                submitChatApproval(approval.id, true, null);
                break;
            }

            // Manual approval: show inline card in the main container (user must act)
            if (thinkingEl) thinkingEl.classList.add('hidden');
            const approvalElId = 'chat-approval-' + approval.id;
            container.innerHTML += `
                <div class="chat-message agent-approval" id="${approvalElId}">
                    <div class="message-header">Approval Required</div>
                    <div class="message-content">
                        <p>${escapeHtml(approval.description)}</p>
                        <div class="approval-detail">
                            <strong>Tool:</strong> ${escapeHtml(approval.tool)}
                            <pre>${escapeHtml(JSON.stringify(approval.args, null, 2))}</pre>
                        </div>
                        <div class="approval-buttons">
                            <button class="btn btn-primary btn-sm" onclick="submitChatApproval('${approval.id}', true, '${approvalElId}')">Approve</button>
                            <button class="btn btn-secondary btn-sm" onclick="submitChatApproval('${approval.id}', false, '${approvalElId}')">Reject</button>
                        </div>
                    </div>
                </div>
            `;
            container.scrollTop = container.scrollHeight;
            break;
        }

        case 'tool_approved':
            if (thinkingEl) thinkingEl.classList.remove('hidden');
            break;

        case 'tool_rejected':
            if (thinkingEl) thinkingEl.classList.remove('hidden');
            updateStatus('Adjusting approach...');
            break;

        case 'tool_result': {
            const resultPreview = typeof event.result === 'string'
                ? event.result.slice(0, 400)
                : JSON.stringify(event.result).slice(0, 400);
            addToLog(`
                <div class="agent-log-entry log-tool-result">
                    <details>
                        <summary>Result from ${escapeHtml(event.tool.replace(/_/g, ' '))}</summary>
                        <pre style="font-size:0.72rem;margin-top:0.25rem;white-space:pre-wrap">${escapeHtml(resultPreview)}${resultPreview.length >= 400 ? '…' : ''}</pre>
                    </details>
                </div>
            `);
            container.scrollTop = container.scrollHeight;
            break;
        }

        case 'response':
            addToLog(`
                <div class="agent-log-entry log-response">${escapeHtml((event.content || '').slice(0, 200))}${(event.content || '').length > 200 ? '…' : ''}</div>
            `);
            updateStatus('Continuing analysis...');
            break;

        case 'complete': {
            // Seal the analysis block: remove spinner, show "done" summary
            if (thinkingEl) {
                const summaryEl = thinkingEl.querySelector('.agent-analysis-summary');
                if (summaryEl) {
                    summaryEl.innerHTML = `
                        <div class="agent-analysis-done">Analysis complete &middot; ${event.iterations || 0} iteration${(event.iterations || 0) !== 1 ? 's' : ''} (click to expand)</div>
                    `;
                }
            }

            // Build sources HTML
            const sources = event.sources || [];
            const sourcesHtml = sources.length > 0
                ? `<div class="message-sources">
                    <strong>Sources:</strong>
                    ${sources.map(src => {
                        const label = escapeHtml(src.source_file) + (src.page ? ` p.${src.page}` : '');
                        const meta = src.metadata || {};
                        let link = null;
                        if (meta.file_source === 'google_drive' && meta.gdrive_file_id) {
                            link = `https://drive.google.com/file/d/${meta.gdrive_file_id}/view`;
                        } else if (src.source_file) {
                            const base = `/api/documents/file/${currentLibraryId}/${encodeURIComponent(src.source_file)}`;
                            link = (src.page && src.source_file.toLowerCase().endsWith('.pdf'))
                                ? `${base}#page=${src.page}` : base;
                        }
                        return link
                            ? `<a class="source-tag source-tag-link" href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">${label}</a>`
                            : `<span class="source-tag">${label}</span>`;
                    }).join('')}
                   </div>`
                : '';

            // Build entities HTML
            const entitiesHtml = buildGraphEntitiesHtml(event.entities || [], currentLibraryId);

            // Build meta HTML
            const metaHtml = `
                <div class="message-meta">
                    <span class="meta-item">${event.iterations || 0} iteration${(event.iterations || 0) !== 1 ? 's' : ''}</span>
                </div>
            `;

            // Add final answer to chat
            container.innerHTML += `
                <div class="chat-message assistant agent-final">
                    <div class="message-header">${escapeHtml(agentName)} ${copyBtnHtml()}</div>
                    <div class="message-content" data-raw="${attrEscape(event.result || '')}">${renderMessageContent(event.result || '')}</div>
                    ${sourcesHtml}
                    ${entitiesHtml}
                    ${metaHtml}
                </div>
            `;

            // Save to history
            chatHistory.push({
                role: 'assistant',
                content: event.result || '',
                sourcesHtml,
                entitiesHtml,
                metaHtml,
                agentName,
            });
            saveChatHistory();

            container.scrollTop = container.scrollHeight;
            break;
        }

        case 'error':
            if (thinkingEl) thinkingEl.remove();
            container.innerHTML += `
                <div class="chat-message agent-error">
                    <div class="message-header">Error ${copyBtnHtml()}</div>
                    <div class="message-content">${escapeHtml(event.message)}</div>
                </div>
            `;
            container.scrollTop = container.scrollHeight;
            break;
    }
}

async function submitChatApproval(approvalId, approved, approvalElId) {
    const reason = approved ? null : prompt('Reason for rejection (optional):');

    try {
        await api('/api/agents/approve', {
            method: 'POST',
            body: JSON.stringify({
                approval_id: approvalId,
                approved: approved,
                reason: reason,
            }),
        });

        // Update the approval card to show resolved status
        const el = document.getElementById(approvalElId);
        if (el) {
            const buttons = el.querySelector('.approval-buttons');
            if (buttons) {
                buttons.innerHTML = approved
                    ? '<span class="approval-resolved approved">Approved</span>'
                    : '<span class="approval-resolved rejected">Rejected</span>';
            }
        }

    } catch (error) {
        showToast('Failed to submit approval: ' + error.message, 'error');
    }
}

// Search
async function performSearch() {
    const query = document.getElementById('search-input').value.trim();

    if (!query) {
        showToast('Please enter a search query', 'warning');
        return;
    }

    if (!currentLibraryId) {
        showToast('Please select a library first', 'warning');
        return;
    }

    const resultsContainer = document.getElementById('search-results');
    resultsContainer.innerHTML = '<p class="placeholder-text">Searching...</p>';

    try {
        const data = await api('/api/search', {
            method: 'POST',
            body: JSON.stringify({
                query,
                library_id: currentLibraryId,
                use_vector_search: document.getElementById('use-vector').checked,
                use_graph_search: document.getElementById('use-graph').checked,
                top_k: Math.max(1, parseInt(document.getElementById('search-top-k').value) || 10),
            }),
        });

        renderSearchResults(data.results);
    } catch (error) {
        resultsContainer.innerHTML = `<p class="placeholder-text">Search failed: ${escapeHtml(error.message)}</p>`;
    }
}

function renderSearchResults(results) {
    const container = document.getElementById('search-results');

    if (results.length === 0) {
        container.innerHTML = '<p class="placeholder-text">No results found</p>';
        return;
    }

    container.innerHTML = results.map(result => `
        <div class="result-item">
            <div class="result-header">
                ${result.file_link
                    ? `<a class="result-source result-source-link" href="${escapeHtml(result.file_link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(result.source_file)}</a>`
                    : `<span class="result-source">${escapeHtml(result.source_file)}</span>`
                }
                <span class="result-score">${(result.score * 100).toFixed(1)}% match</span>
            </div>
            <div class="result-meta">
                ${result.page ? `Page ${result.page}` : ''}
                ${result.chunk_index ? `| Chunk ${result.chunk_index}` : ''}
                | Source: ${result.source}
            </div>
            ${result.related_entities && result.related_entities.length > 0 ? `
                <div class="result-entities">
                    <span class="result-entities-label">Relationships:</span>
                    ${result.related_entities.map(e => `<span class="entity-rel-tag">${escapeHtml(e)}</span>`).join('')}
                </div>
            ` : ''}
        </div>
    `).join('');
}

// Document Ingestion with SSE Progress
async function uploadFile(file) {
    if (!currentLibraryId) {
        showToast('Please select a library first', 'warning');
        return;
    }

    const progressContainer = document.getElementById('upload-progress');
    const embeddingFill = document.getElementById('embedding-progress-fill');
    const embeddingPercent = document.getElementById('embedding-percent');
    const graphFill = document.getElementById('graph-progress-fill');
    const graphPercent = document.getElementById('graph-percent');
    const progressText = document.getElementById('progress-text');

    // Reset progress
    progressContainer.classList.remove('hidden');
    embeddingFill.style.width = '0%';
    embeddingPercent.textContent = '0%';
    graphFill.style.width = '0%';
    graphPercent.textContent = '0%';
    progressText.textContent = `Processing ${file.name}...`;

    let lastStage = null;
    try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('library_id', currentLibraryId);

        // Use SSE streaming endpoint
        const response = await fetch('/api/documents/upload/stream', {
            method: 'POST',
            body: formData,
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Upload failed' }));
            throw new Error(error.detail);
        }

        // Process SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        lastStage = handleProgressEvent(data, embeddingFill, embeddingPercent, graphFill, graphPercent, progressText);
                    } catch (e) {
                        console.warn('Failed to parse SSE data:', e);
                    }
                }
            }
        }

        if (lastStage === 'error') {
            showToast(`Import failed for ${file.name}`, 'error');
        } else {
            showToast(`Successfully imported ${file.name}`, 'success');
        }
        loadSources();
        loadLibraries();

    } catch (error) {
        showToast(`Failed to import ${file.name}: ${error.message}`, 'error');
    } finally {
        setTimeout(() => {
            progressContainer.classList.add('hidden');
        }, 2000);
    }
}

function handleProgressEvent(data, embeddingFill, embeddingPercent, graphFill, graphPercent, progressText) {
    const embLabel = document.getElementById('embedding-stage-label');
    const graphLabel = document.getElementById('graph-stage-label');

    progressText.textContent = data.message || 'Processing...';
    // Return stage so callers can detect error vs complete

    switch (data.stage) {
        case 'parsing':
            if (embLabel) embLabel.textContent = 'Parsing';
            embeddingFill.style.width = '5%';
            embeddingPercent.textContent = '5%';
            break;

        case 'embedding': {
            if (embLabel) embLabel.textContent = `Embedding (${data.current}/${data.total} chunks)`;
            // Scale embedding progress across 0–70% of bar so storing phase is visible
            const embFill = Math.round((data.percent || 0) * 0.7);
            embeddingFill.style.width = `${embFill}%`;
            embeddingPercent.textContent = `${Math.round(data.percent || 0)}%`;
            break;
        }

        case 'storing': {
            if (embLabel) embLabel.textContent = `Storing (${data.current}/${data.total} vectors)`;
            // Scale storing progress across 70–100% of bar
            const storeFill = 70 + Math.round((data.percent || 0) * 0.3);
            embeddingFill.style.width = `${storeFill}%`;
            embeddingPercent.textContent = `${Math.round(data.percent || 0)}%`;
            break;
        }

        case 'graph': {
            if (graphLabel) graphLabel.textContent = `Graph (${data.current}/${data.total} chunks)`;
            const gPct = Math.round(data.percent || 0);
            graphFill.style.width = `${gPct}%`;
            graphPercent.textContent = `${gPct}%`;
            break;
        }

        case 'complete':
            if (embLabel) embLabel.textContent = 'Vectorization';
            if (graphLabel) graphLabel.textContent = 'Graph Extraction';
            embeddingFill.style.width = '100%';
            embeddingPercent.textContent = '100%';
            graphFill.style.width = '100%';
            graphPercent.textContent = '100%';
            progressText.textContent = `Done! ${data.chunks_processed} chunks, ${data.graph_nodes} nodes, ${data.graph_relationships} relationships`;
            break;

        case 'error':
            progressText.textContent = `Error: ${data.message}`;
            break;
    }
    return data.stage;
}

async function ingestText() {
    const text = document.getElementById('paste-input').value.trim();
    const sourceName = document.getElementById('source-name').value.trim() || 'pasted_text';

    if (!text) {
        showToast('Please enter some text', 'warning');
        return;
    }

    if (!currentLibraryId) {
        showToast('Please select a library first', 'warning');
        return;
    }

    const progressContainer = document.getElementById('upload-progress');
    const embeddingFill = document.getElementById('embedding-progress-fill');
    const embeddingPercent = document.getElementById('embedding-percent');
    const graphFill = document.getElementById('graph-progress-fill');
    const graphPercent = document.getElementById('graph-percent');
    const progressText = document.getElementById('progress-text');

    // Reset and show progress
    progressContainer.classList.remove('hidden');
    embeddingFill.style.width = '0%';
    embeddingPercent.textContent = '0%';
    graphFill.style.width = '0%';
    graphPercent.textContent = '0%';
    progressText.textContent = `Processing ${sourceName}...`;

    let lastStage = null;
    try {
        const response = await fetch('/api/documents/text/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                text,
                library_id: currentLibraryId,
                source_name: sourceName,
            }),
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Import failed' }));
            throw new Error(error.detail);
        }

        // Process SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        lastStage = handleProgressEvent(data, embeddingFill, embeddingPercent, graphFill, graphPercent, progressText);
                    } catch (e) {
                        console.warn('Failed to parse SSE data:', e);
                    }
                }
            }
        }

        if (lastStage === 'error') {
            showToast(`Import failed for ${sourceName}`, 'error');
        } else {
            showToast('Text imported successfully', 'success');
        }
        document.getElementById('paste-input').value = '';
        document.getElementById('source-name').value = '';
        loadSources();
        loadLibraries();

    } catch (error) {
        showToast('Failed to import text: ' + error.message, 'error');
    } finally {
        setTimeout(() => {
            progressContainer.classList.add('hidden');
        }, 2000);
    }
}

// Settings
function switchSettingsTab(tabId) {
    document.querySelectorAll('.settings-tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.settings-panel').forEach(panel => panel.classList.remove('active'));
    document.querySelector(`.settings-tab-btn[onclick="switchSettingsTab('${tabId}')"]`).classList.add('active');
    document.getElementById(`settings-panel-${tabId}`).classList.add('active');
}

async function loadSettings() {
    try {
        const settings = await api('/api/settings');
        document.getElementById('api-url').value = settings.api_base_url;
        document.getElementById('api-key').value = '';
        document.getElementById('api-key').placeholder = settings.api_key_masked;
        document.getElementById('embedding-model').value = settings.embedding_model;
        document.getElementById('chat-model').value = settings.chat_model;
        document.getElementById('chunk-size').value = settings.chunk_size;
        document.getElementById('chunk-overlap').value = settings.chunk_overlap;
        document.getElementById('max-conversation-history').value = settings.max_conversation_history ?? 6;

        // Proxy settings
        document.getElementById('proxy-url').value = settings.proxy_url || '';
        document.getElementById('proxy-username').value = settings.proxy_username || '';
        document.getElementById('proxy-password').value = '';
        document.getElementById('proxy-password').placeholder = settings.proxy_password_masked || '';
        document.getElementById('ssl-certificate-path').value = settings.ssl_certificate_path || '';

        // Graph settings
        if (settings.graph) {
            document.getElementById('enable-graph').checked = settings.graph.enable_graph_extraction !== false;
            document.getElementById('extraction-method').value = settings.graph.extraction_method || 'regex';
            document.getElementById('extract-proper-nouns').checked = settings.graph.extract_proper_nouns !== false;
            document.getElementById('extract-emails').checked = settings.graph.extract_emails === true;
            document.getElementById('extract-urls').checked = settings.graph.extract_urls === true;
            document.getElementById('max-entities').value = settings.graph.max_entities_per_chunk || 15;
        }

        // Load relationship settings
        await loadRelationshipSettings();

        // Google Drive settings
        if (settings.google_drive) {
            document.getElementById('gdrive-export-format').value = settings.google_drive.export_format || 'pdf';
        }

        // Update Google Drive status in settings
        loadGoogleDriveStatus();
    } catch (error) {
        showToast('Failed to load settings: ' + error.message, 'error');
    }
}

async function loadRelationshipSettings() {
    try {
        const relSettings = await api('/api/settings/relationships');

        // Document structure
        document.getElementById('rel-doc-structure').checked = relSettings.document_structure?.enabled !== false;
        document.getElementById('rel-next-chunk').checked = relSettings.document_structure?.next_chunk !== false;
        document.getElementById('rel-same-page').checked = relSettings.document_structure?.same_page !== false;

        // Component
        document.getElementById('rel-component').checked = relSettings.component?.enabled !== false;
        document.getElementById('rel-part-of').checked = relSettings.component?.part_of !== false;
        document.getElementById('rel-connects-to').checked = relSettings.component?.connects_to !== false;
        document.getElementById('rel-supplies-to').checked = relSettings.component?.supplies_to !== false;
        document.getElementById('rel-controls').checked = relSettings.component?.controls !== false;

        // Process
        document.getElementById('rel-process').checked = relSettings.process?.enabled !== false;
        document.getElementById('rel-precedes').checked = relSettings.process?.precedes !== false;
        document.getElementById('rel-triggers').checked = relSettings.process?.triggers !== false;
        document.getElementById('rel-requires').checked = relSettings.process?.requires !== false;

        // Semantic
        document.getElementById('rel-semantic').checked = relSettings.semantic?.enabled !== false;
        document.getElementById('rel-co-occurs-sentence').checked = relSettings.semantic?.co_occurs_sentence !== false;
        document.getElementById('rel-co-occurs-chunk').checked = relSettings.semantic?.co_occurs_chunk !== false;

        // Hierarchy
        document.getElementById('rel-hierarchy').checked = relSettings.hierarchy?.enabled === true;
        document.getElementById('rel-is-a').checked = relSettings.hierarchy?.is_a !== false;
        document.getElementById('rel-has-property').checked = relSettings.hierarchy?.has_property !== false;
    } catch (error) {
        console.error('Failed to load relationship settings:', error);
    }
}

async function saveSettings() {
    const updates = {};

    const apiUrl = document.getElementById('api-url').value.trim();
    const apiKey = document.getElementById('api-key').value.trim();
    const embeddingModel = document.getElementById('embedding-model').value.trim();
    const chatModel = document.getElementById('chat-model').value.trim();
    const chunkSize = parseInt(document.getElementById('chunk-size').value);
    const chunkOverlap = parseInt(document.getElementById('chunk-overlap').value);

    if (apiUrl) updates.api_base_url = apiUrl;
    if (apiKey) updates.api_key = apiKey;
    if (embeddingModel) updates.embedding_model = embeddingModel;
    if (chatModel) updates.chat_model = chatModel;
    if (!isNaN(chunkSize)) updates.chunk_size = chunkSize;
    if (!isNaN(chunkOverlap)) updates.chunk_overlap = chunkOverlap;
    const maxConvHistory = parseInt(document.getElementById('max-conversation-history').value);
    if (!isNaN(maxConvHistory)) updates.max_conversation_history = maxConvHistory;

    // Proxy settings — send empty string to clear a field, null means "don't touch"
    const proxyUrl = document.getElementById('proxy-url').value.trim();
    const proxyUsername = document.getElementById('proxy-username').value.trim();
    const proxyPassword = document.getElementById('proxy-password').value;  // don't trim passwords
    const sslCertPath = document.getElementById('ssl-certificate-path').value.trim();
    updates.proxy_url = proxyUrl || null;
    updates.proxy_username = proxyUsername || null;
    if (proxyPassword) updates.proxy_password = proxyPassword;  // only send if user typed something
    updates.ssl_certificate_path = sslCertPath || null;

    // Graph settings
    updates.graph = {
        enable_graph_extraction: document.getElementById('enable-graph').checked,
        extraction_method: document.getElementById('extraction-method').value,
        extract_proper_nouns: document.getElementById('extract-proper-nouns').checked,
        extract_emails: document.getElementById('extract-emails').checked,
        extract_urls: document.getElementById('extract-urls').checked,
        max_entities_per_chunk: parseInt(document.getElementById('max-entities').value) || 15,
    };

    try {
        await api('/api/settings', {
            method: 'PUT',
            body: JSON.stringify(updates),
        });

        // Save relationship settings separately
        await saveRelationshipSettings();

        showToast('Settings saved successfully', 'success');
        closeModal('settings-modal');
    } catch (error) {
        showToast('Failed to save settings: ' + error.message, 'error');
    }
}

async function saveRelationshipSettings() {
    const relUpdates = {
        // Document structure
        document_structure_enabled: document.getElementById('rel-doc-structure').checked,
        next_chunk: document.getElementById('rel-next-chunk').checked,
        same_page: document.getElementById('rel-same-page').checked,

        // Component
        component_enabled: document.getElementById('rel-component').checked,
        part_of: document.getElementById('rel-part-of').checked,
        connects_to: document.getElementById('rel-connects-to').checked,
        supplies_to: document.getElementById('rel-supplies-to').checked,
        controls: document.getElementById('rel-controls').checked,

        // Process
        process_enabled: document.getElementById('rel-process').checked,
        precedes: document.getElementById('rel-precedes').checked,
        triggers: document.getElementById('rel-triggers').checked,
        requires: document.getElementById('rel-requires').checked,

        // Semantic
        semantic_enabled: document.getElementById('rel-semantic').checked,
        co_occurs_sentence: document.getElementById('rel-co-occurs-sentence').checked,
        co_occurs_chunk: document.getElementById('rel-co-occurs-chunk').checked,

        // Hierarchy
        hierarchy_enabled: document.getElementById('rel-hierarchy').checked,
        is_a: document.getElementById('rel-is-a').checked,
        has_property: document.getElementById('rel-has-property').checked,
    };

    await api('/api/settings/relationships', {
        method: 'PUT',
        body: JSON.stringify(relUpdates),
    });
}

async function testConnection() {
    const statusEl = document.getElementById('connection-status');
    statusEl.textContent = 'Testing...';
    statusEl.style.color = '';

    try {
        const result = await api('/api/settings/test', { method: 'POST' });

        if (result.success) {
            statusEl.textContent = 'Connection successful!';
            statusEl.style.color = 'var(--success-color)';
        } else {
            statusEl.textContent = result.message;
            statusEl.style.color = 'var(--warning-color)';
        }
    } catch (error) {
        statusEl.textContent = 'Connection failed';
        statusEl.style.color = 'var(--error-color)';
    }
}

// Cached model list shared by both model fields
let _availableModels = [];

async function loadModels() {
    const btn = document.getElementById('btn-load-models');
    const statusEl = document.getElementById('models-load-status');

    btn.disabled = true;
    statusEl.textContent = 'Loading models...';
    statusEl.style.color = '';

    try {
        const result = await api('/api/settings/models');
        _availableModels = result.models || [];
        statusEl.textContent = `${_availableModels.length} model${_availableModels.length !== 1 ? 's' : ''} available`;
        statusEl.style.color = 'var(--success-color)';
    } catch (error) {
        statusEl.textContent = 'Failed to load: ' + error.message;
        statusEl.style.color = 'var(--error-color)';
    } finally {
        btn.disabled = false;
    }
}

function toggleModelDropdown(inputId) {
    const existingDropdown = document.getElementById(`${inputId}-dropdown`);
    // Close if already open
    if (existingDropdown) {
        existingDropdown.remove();
        return;
    }
    // Close any other open dropdowns
    document.querySelectorAll('.model-dropdown-list').forEach(d => d.remove());

    if (_availableModels.length === 0) {
        const statusEl = document.getElementById('models-load-status');
        statusEl.textContent = 'Click "Load models from API" first';
        statusEl.style.color = 'var(--warning-color)';
        return;
    }
    _openModelDropdown(inputId, _availableModels);
}

function _openModelDropdown(inputId, models) {
    const input = document.getElementById(inputId);
    const container = input.closest('.model-input-group');

    const dropdown = document.createElement('div');
    dropdown.id = `${inputId}-dropdown`;
    dropdown.className = 'model-dropdown-list';

    const renderItems = (filter) => {
        dropdown.innerHTML = '';
        const filtered = filter
            ? models.filter(m => m.toLowerCase().includes(filter.toLowerCase()))
            : models;

        if (filtered.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'model-dropdown-empty';
            empty.textContent = 'No models match';
            dropdown.appendChild(empty);
            return;
        }

        filtered.forEach(m => {
            const item = document.createElement('div');
            item.className = 'model-dropdown-item';
            item.textContent = m;
            item.addEventListener('mousedown', (e) => {
                e.preventDefault(); // prevent input blur before click registers
                input.value = m;
                dropdown.remove();
                input.removeEventListener('input', onInput);
            });
            dropdown.appendChild(item);
        });
    };

    renderItems(''); // show all initially

    container.appendChild(dropdown);

    // Filter as user types
    const onInput = () => renderItems(input.value);
    input.addEventListener('input', onInput);

    // Close on outside click
    const onOutsideClick = (e) => {
        if (!container.contains(e.target)) {
            dropdown.remove();
            input.removeEventListener('input', onInput);
            document.removeEventListener('mousedown', onOutsideClick);
        }
    };
    setTimeout(() => document.addEventListener('mousedown', onOutsideClick), 0);
}

// Utilities
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// =============================================================================
// Chat Utilities — Copy & Clear
// =============================================================================

function copyBtnHtml() {
    return '<button class="btn-copy-msg" onclick="copyMessageText(this)" title="Copy to clipboard">&#x2398;</button>';
}

function copyMessageText(btn) {
    const msg = btn.closest('.chat-message');
    if (!msg) return;
    const content = msg.querySelector('.message-content');
    if (!content) return;
    // Prefer raw markdown (data-raw); fall back to visible text for user messages
    const text = content.dataset.raw ?? (content.innerText || content.textContent);
    navigator.clipboard.writeText(text).then(() => {
        btn.textContent = '\u2713';
        btn.classList.add('copied');
        setTimeout(() => { btn.innerHTML = '&#x2398;'; btn.classList.remove('copied'); }, 1500);
    }).catch(() => {
        // Fallback for insecure contexts
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        btn.textContent = '\u2713';
        btn.classList.add('copied');
        setTimeout(() => { btn.innerHTML = '&#x2398;'; btn.classList.remove('copied'); }, 1500);
    });
}

function clearChat() {
    chatHistory = [];
    if (currentLibraryId) {
        try { localStorage.removeItem('chat_history_' + currentLibraryId); } catch (e) { /* ignore */ }
    }
    const container = document.getElementById('chat-messages');
    if (!container) return;
    container.innerHTML = `
        <div class="chat-welcome">
            <h3>Ask a Question</h3>
            <p>Ask questions about your documents and get AI-generated answers with citations.</p>
        </div>
    `;
    currentChatAgentTaskId = null;
}

// =============================================================================
// Google Drive Integration
// =============================================================================

let gdriveAuthUrl = null;
let gdriveCurrentFolder = 'root';
let gdriveSelectedFiles = new Set();

async function loadGoogleDriveStatus() {
    try {
        const status = await api('/api/google-drive/status');
        updateGoogleDriveUI(status);
    } catch (error) {
        console.error('Failed to load Google Drive status:', error);
    }
}

function updateGoogleDriveUI(status) {
    const statusIndicator = document.querySelector('#gdrive-status .status-indicator');
    const statusText = document.getElementById('gdrive-status-text');
    const connectBtn = document.getElementById('btn-gdrive-connect');
    const disconnectBtn = document.getElementById('btn-gdrive-disconnect');
    const authCodeSection = document.getElementById('gdrive-auth-code-section');
    const browseBtn = document.getElementById('btn-browse-gdrive');
    const hint = document.getElementById('gdrive-hint');

    if (!status.available) {
        statusText.textContent = 'Google API not installed';
        statusIndicator.className = 'status-indicator status-unavailable';
        connectBtn.disabled = true;
        browseBtn.disabled = true;
        hint.textContent = 'Install google-api-python-client package';
        return;
    }

    if (status.authenticated) {
        statusText.textContent = `Connected as ${status.email || 'Unknown'}`;
        statusIndicator.className = 'status-indicator status-connected';
        connectBtn.classList.add('hidden');
        disconnectBtn.classList.remove('hidden');
        authCodeSection.classList.add('hidden');
        browseBtn.disabled = false;
        hint.textContent = '';
    } else {
        statusText.textContent = status.has_credentials ? 'Not connected' : 'No credentials uploaded';
        statusIndicator.className = 'status-indicator status-disconnected';
        connectBtn.classList.remove('hidden');
        connectBtn.disabled = !status.has_credentials;
        disconnectBtn.classList.add('hidden');
        browseBtn.disabled = true;
        hint.textContent = status.has_credentials
            ? 'Connect Google Drive in Settings to enable'
            : 'Upload credentials.json in Settings first';
    }
}

async function uploadGoogleCredentials() {
    const fileInput = document.getElementById('gdrive-credentials');
    const file = fileInput.files[0];

    if (!file) {
        showToast('Please select a credentials.json file', 'warning');
        return;
    }

    try {
        const text = await file.text();

        const response = await api('/api/google-drive/auth/credentials', {
            method: 'POST',
            body: JSON.stringify({ credentials_json: text }),
        });

        if (response.success) {
            gdriveAuthUrl = response.auth_url;
            showToast('Credentials uploaded. Click "Open Google Auth" to authorize.', 'success');

            // Show auth code section
            document.getElementById('gdrive-auth-code-section').classList.remove('hidden');

            // Open auth URL
            window.open(gdriveAuthUrl, '_blank');
        } else {
            showToast(response.message, 'error');
        }
    } catch (error) {
        showToast('Failed to upload credentials: ' + error.message, 'error');
    }
}

async function startGoogleAuth() {
    try {
        const response = await api('/api/google-drive/auth/start', {
            method: 'POST',
        });

        if (response.success) {
            gdriveAuthUrl = response.auth_url;
            document.getElementById('gdrive-auth-code-section').classList.remove('hidden');
            window.open(gdriveAuthUrl, '_blank');
            showToast('Authorization page opened. Paste the code after granting access.', 'info');
        } else {
            showToast(response.message, 'error');
        }
    } catch (error) {
        showToast('Failed to start auth: ' + error.message, 'error');
    }
}

async function completeGoogleAuth() {
    const authCode = document.getElementById('gdrive-auth-code').value.trim();

    if (!authCode) {
        showToast('Please paste the authorization code', 'warning');
        return;
    }

    try {
        const response = await api('/api/google-drive/auth/complete', {
            method: 'POST',
            body: JSON.stringify({ auth_code: authCode }),
        });

        if (response.success) {
            showToast(`Connected to Google Drive as ${response.email}`, 'success');
            document.getElementById('gdrive-auth-code').value = '';
            document.getElementById('gdrive-auth-code-section').classList.add('hidden');
            loadGoogleDriveStatus();
        } else {
            showToast(response.message, 'error');
        }
    } catch (error) {
        showToast('Failed to complete auth: ' + error.message, 'error');
    }
}

async function disconnectGoogleDrive() {
    if (!confirm('Disconnect from Google Drive? You will need to re-authorize to use it again.')) {
        return;
    }

    try {
        await api('/api/google-drive/disconnect', { method: 'POST' });
        showToast('Disconnected from Google Drive', 'success');
        loadGoogleDriveStatus();
    } catch (error) {
        showToast('Failed to disconnect: ' + error.message, 'error');
    }
}

async function openGoogleDriveBrowser() {
    if (!currentLibraryId) {
        showToast('Please select a library first', 'warning');
        return;
    }

    gdriveCurrentFolder = 'root';
    gdriveSelectedFiles.clear();
    openModal('gdrive-browser-modal');
    await loadGoogleDriveFolder('root');
}

async function loadGoogleDriveFolder(folderId) {
    gdriveCurrentFolder = folderId;
    const fileList = document.getElementById('gdrive-file-list');

    fileList.innerHTML = `
        <div class="gdrive-loading">
            <div class="spinner"></div>
            <span>Loading files...</span>
        </div>
    `;

    try {
        const response = await api(`/api/google-drive/files?folder_id=${encodeURIComponent(folderId)}`);

        if (response.error) {
            fileList.innerHTML = `<div class="gdrive-error">${escapeHtml(response.error)}</div>`;
            return;
        }

        // Update breadcrumb
        updateGdriveBreadcrumb(response.path);

        // Render files
        if (response.files.length === 0) {
            fileList.innerHTML = '<div class="gdrive-empty">This folder is empty</div>';
            return;
        }

        fileList.innerHTML = response.files.map(file => {
            const isSelected = gdriveSelectedFiles.has(file.id);
            const icon = file.isFolder ? getFolderIcon() : getFileIcon(file.mimeType);
            const sizeStr = file.size ? formatFileSize(parseInt(file.size)) : '';
            const typeLabel = file.typeLabel ? ` (${file.typeLabel})` : '';

            if (file.isFolder) {
                return `
                    <div class="gdrive-item gdrive-folder" onclick="loadGoogleDriveFolder('${file.id}')">
                        ${icon}
                        <span class="gdrive-item-name">${escapeHtml(file.name)}</span>
                    </div>
                `;
            } else {
                const disabledClass = file.supported ? '' : 'gdrive-item-unsupported';
                return `
                    <div class="gdrive-item gdrive-file ${disabledClass} ${isSelected ? 'selected' : ''}"
                         onclick="toggleGdriveFile('${file.id}', ${file.supported})">
                        <input type="checkbox" class="gdrive-checkbox"
                               ${isSelected ? 'checked' : ''}
                               ${file.supported ? '' : 'disabled'}
                               onclick="event.stopPropagation()">
                        ${icon}
                        <span class="gdrive-item-name">${escapeHtml(file.name)}${typeLabel}</span>
                        <span class="gdrive-item-size">${sizeStr}</span>
                        ${file.supported ? '' : '<span class="gdrive-unsupported-badge">Unsupported</span>'}
                    </div>
                `;
            }
        }).join('');

        updateGdriveSelectionCount();

    } catch (error) {
        fileList.innerHTML = `<div class="gdrive-error">Failed to load folder: ${escapeHtml(error.message)}</div>`;
    }
}

function updateGdriveBreadcrumb(path) {
    const breadcrumb = document.getElementById('gdrive-breadcrumb');
    breadcrumb.innerHTML = path.map((item, index) => {
        const isLast = index === path.length - 1;
        return `
            <span class="breadcrumb-item ${isLast ? 'active' : ''}"
                  ${isLast ? '' : `onclick="loadGoogleDriveFolder('${item.id}')"`}>
                ${escapeHtml(item.name)}
            </span>
            ${isLast ? '' : '<span class="breadcrumb-sep">/</span>'}
        `;
    }).join('');
}

function toggleGdriveFile(fileId, supported) {
    if (!supported) return;

    if (gdriveSelectedFiles.has(fileId)) {
        gdriveSelectedFiles.delete(fileId);
    } else {
        gdriveSelectedFiles.add(fileId);
    }

    // Update UI
    const item = document.querySelector(`.gdrive-file[onclick*="${fileId}"]`);
    if (item) {
        item.classList.toggle('selected', gdriveSelectedFiles.has(fileId));
        const checkbox = item.querySelector('.gdrive-checkbox');
        if (checkbox) checkbox.checked = gdriveSelectedFiles.has(fileId);
    }

    updateGdriveSelectionCount();
}

function updateGdriveSelectionCount() {
    const count = gdriveSelectedFiles.size;
    document.getElementById('gdrive-selection-count').textContent =
        count === 0 ? 'No files selected' :
        count === 1 ? '1 file selected' :
        `${count} files selected`;

    document.getElementById('btn-gdrive-import').disabled = count === 0;
}

async function importSelectedGdriveFiles() {
    if (gdriveSelectedFiles.size === 0) {
        showToast('No files selected', 'warning');
        return;
    }

    closeModal('gdrive-browser-modal');

    const progressContainer = document.getElementById('upload-progress');
    const embeddingFill = document.getElementById('embedding-progress-fill');
    const embeddingPercent = document.getElementById('embedding-percent');
    const graphFill = document.getElementById('graph-progress-fill');
    const graphPercent = document.getElementById('graph-percent');
    const progressText = document.getElementById('progress-text');

    // Reset progress
    progressContainer.classList.remove('hidden');
    embeddingFill.style.width = '0%';
    embeddingPercent.textContent = '0%';
    graphFill.style.width = '0%';
    graphPercent.textContent = '0%';
    progressText.textContent = 'Starting Google Drive import...';

    try {
        const response = await fetch('/api/google-drive/import/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                file_ids: Array.from(gdriveSelectedFiles),
                library_id: currentLibraryId,
            }),
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Import failed' }));
            throw new Error(error.detail);
        }

        // Process SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        handleGdriveProgressEvent(data, embeddingFill, embeddingPercent, graphFill, graphPercent, progressText);
                    } catch (e) {
                        console.warn('Failed to parse SSE data:', e);
                    }
                }
            }
        }

        showToast('Google Drive import completed', 'success');
        gdriveSelectedFiles.clear();
        loadSources();
        loadLibraries();

    } catch (error) {
        showToast('Import failed: ' + error.message, 'error');
    } finally {
        setTimeout(() => {
            progressContainer.classList.add('hidden');
        }, 2000);
    }
}

function handleGdriveProgressEvent(data, embeddingFill, embeddingPercent, graphFill, graphPercent, progressText) {
    progressText.textContent = data.message || 'Processing...';

    switch (data.stage) {
        case 'downloading':
            embeddingFill.style.width = '0%';
            break;

        case 'parsing':
            embeddingFill.style.width = '5%';
            embeddingPercent.textContent = '5%';
            break;

        case 'embedding':
            const embPercent = Math.round((data.current / data.total_files) * 50 + 5);
            embeddingFill.style.width = `${embPercent}%`;
            embeddingPercent.textContent = `${embPercent}%`;
            break;

        case 'storing':
            embeddingFill.style.width = '60%';
            embeddingPercent.textContent = '60%';
            break;

        case 'graph':
            const graphPct = Math.round((data.current / data.total_files) * 100);
            graphFill.style.width = `${graphPct}%`;
            graphPercent.textContent = `${graphPct}%`;
            break;

        case 'file_complete':
            const completePct = Math.round((data.current / data.total) * 100);
            embeddingFill.style.width = `${completePct}%`;
            embeddingPercent.textContent = `${completePct}%`;
            break;

        case 'complete':
            embeddingFill.style.width = '100%';
            embeddingPercent.textContent = '100%';
            graphFill.style.width = '100%';
            graphPercent.textContent = '100%';
            progressText.textContent = `Done! ${data.files_processed}/${data.total_files} files, ${data.chunks_processed} chunks`;
            break;

        case 'error':
        case 'warning':
            progressText.textContent = data.message;
            break;
    }
}

function getFolderIcon() {
    return `<svg class="gdrive-icon" viewBox="0 0 24 24" width="20" height="20">
        <path fill="currentColor" d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/>
    </svg>`;
}

function getFileIcon(mimeType) {
    let path = 'M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z';

    if (mimeType.includes('pdf')) {
        path = 'M20 2H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-8.5 7.5c0 .83-.67 1.5-1.5 1.5H9v2H7.5V7H10c.83 0 1.5.67 1.5 1.5v1zm5 2c0 .83-.67 1.5-1.5 1.5h-2.5V7H15c.83 0 1.5.67 1.5 1.5v3zm4-3H19v1h1.5V11H19v2h-1.5V7h3v1.5zM9 9.5h1v-1H9v1zM4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6zm10 5.5h1v-3h-1v3z';
    } else if (mimeType.includes('spreadsheet') || mimeType.includes('excel')) {
        path = 'M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V5h14v14zm-6-2h2V7h-4v2h2v8z';
    } else if (mimeType.includes('document') || mimeType.includes('word')) {
        path = 'M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z';
    }

    return `<svg class="gdrive-icon" viewBox="0 0 24 24" width="20" height="20">
        <path fill="currentColor" d="${path}"/>
    </svg>`;
}

function formatFileSize(bytes) {
    if (!bytes || isNaN(bytes)) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
}

// ==================== AGENTS ====================

// Agent state
let agents = [];
let currentAgentTaskId = null;
let currentApprovalId = null;
let agentEventSource = null;

async function loadAgents() {
    try {
        const data = await api('/api/agents/');
        agents = data.agents;
        renderAgentsList();
        populateAgentSelect();
    } catch (error) {
        console.error('Failed to load agents:', error);
    }
}

function renderAgentsList() {
    const list = document.getElementById('agents-list');
    if (!list) return;

    list.innerHTML = agents.map(agent => `
        <li class="agent-item ${agent.is_template ? 'agent-template' : ''}" data-id="${agent.id}">
            <div class="agent-info">
                <span class="agent-name">${escapeHtml(agent.name)}</span>
                ${agent.is_template ? '<span class="agent-badge">Template</span>' : ''}
                <span class="agent-description">${escapeHtml(agent.description || '')}</span>
            </div>
            <div class="agent-actions">
                ${agent.is_template ?
                    `<button class="btn btn-sm" onclick="editAgent('${agent.id}')">Edit</button>
                     <button class="btn btn-sm" onclick="cloneTemplate('${agent.id}', '${escapeHtml(agent.name)}')">Clone</button>` :
                    `<button class="btn btn-sm" onclick="editAgent('${agent.id}')">Edit</button>
                     <button class="btn btn-sm btn-danger" onclick="deleteAgent('${agent.id}')">Delete</button>`
                }
            </div>
        </li>
    `).join('');
}

function populateAgentSelect() {
    const select = document.getElementById('agent-select');
    if (!select) return;

    select.innerHTML = '<option value="">-- Select an agent --</option>' +
        agents.map(agent => `
            <option value="${agent.id}">${escapeHtml(agent.name)}${agent.is_template ? ' (Template)' : ''}</option>
        `).join('');

    // Also populate the chat agent selector
    const chatSelect = document.getElementById('chat-agent-select');
    if (chatSelect) {
        chatSelect.innerHTML = '<option value="">None (RAG)</option>' +
            agents.map(agent => `
                <option value="${agent.id}">${escapeHtml(agent.name)}</option>
            `).join('');
    }
}

function onChatAgentChanged() {
    const agentId = document.getElementById('chat-agent-select').value;
    const autoLabel = document.getElementById('chat-auto-approve-label');
    const infoBtn = document.getElementById('btn-agent-info');

    if (agentId) {
        autoLabel.classList.remove('hidden');
        infoBtn.classList.remove('hidden');
    } else {
        autoLabel.classList.add('hidden');
        infoBtn.classList.add('hidden');
    }
}

function showAgentInfo() {
    const agentId = document.getElementById('chat-agent-select').value;
    if (!agentId) return;

    const agent = agents.find(a => a.id === agentId);
    if (!agent) return;

    document.getElementById('agent-info-title').textContent = agent.name;
    document.getElementById('agent-info-description').textContent = agent.description || 'No description.';
    document.getElementById('agent-info-prompt').textContent = agent.system_prompt;

    // Tools list
    const toolsList = document.getElementById('agent-info-tools');
    toolsList.innerHTML = (agent.tools || []).map(t =>
        `<li>${escapeHtml(t.replace(/_/g, ' '))}</li>`
    ).join('') || '<li class="muted">No tools configured</li>';

    // Parameters
    document.getElementById('agent-info-params').innerHTML = `
        <div class="param-row"><span>Approval mode:</span> <strong>${escapeHtml(agent.approval_mode)}</strong></div>
        <div class="param-row"><span>Max iterations:</span> <strong>${agent.max_iterations}</strong></div>
        <div class="param-row"><span>Temperature:</span> <strong>${agent.temperature}</strong></div>
        <div class="param-row"><span>Type:</span> <strong>${agent.is_template ? 'Template' : 'Custom'}</strong></div>
    `;

    openModal('agent-info-modal');
}

function openAgentEditor(agentData = null) {
    const isTemplate = agentData?.is_template ?? false;
    let title = 'Create Agent';
    if (agentData) title = isTemplate ? 'Edit Template' : 'Edit Agent';
    document.getElementById('agent-editor-title').textContent = title;

    const notice = document.getElementById('agent-template-notice');
    if (notice) notice.classList.toggle('hidden', !isTemplate);

    document.getElementById('edit-agent-id').value = agentData ? agentData.id : '';
    document.getElementById('agent-name').value = agentData ? agentData.name : '';
    document.getElementById('agent-description').value = agentData ? agentData.description : '';
    document.getElementById('agent-system-prompt').value = agentData ? agentData.system_prompt : '';
    document.getElementById('agent-approval-mode').value = agentData ? agentData.approval_mode : 'always';
    document.getElementById('agent-max-iterations').value = agentData ? agentData.max_iterations : 10;
    document.getElementById('agent-temperature').value = agentData ? agentData.temperature : 0.3;

    // Set tool checkboxes
    document.querySelectorAll('input[name="agent-tool"]').forEach(cb => {
        cb.checked = agentData ? agentData.tools.includes(cb.value) : false;
    });

    openModal('agent-editor-modal');
}

async function editAgent(agentId) {
    try {
        const agent = await api(`/api/agents/${agentId}`);
        openAgentEditor(agent);
    } catch (error) {
        showToast('Failed to load agent: ' + error.message, 'error');
    }
}

async function saveAgent() {
    const agentId = document.getElementById('edit-agent-id').value;
    const name = document.getElementById('agent-name').value.trim();
    const description = document.getElementById('agent-description').value.trim();
    const systemPrompt = document.getElementById('agent-system-prompt').value.trim();
    const approvalMode = document.getElementById('agent-approval-mode').value;
    const maxIterations = parseInt(document.getElementById('agent-max-iterations').value);
    const temperature = parseFloat(document.getElementById('agent-temperature').value);

    const tools = [];
    document.querySelectorAll('input[name="agent-tool"]:checked').forEach(cb => {
        tools.push(cb.value);
    });

    if (!name) {
        showToast('Agent name is required', 'error');
        return;
    }
    if (!systemPrompt) {
        showToast('System prompt is required', 'error');
        return;
    }

    const data = {
        name,
        description,
        system_prompt: systemPrompt,
        tools,
        approval_mode: approvalMode,
        max_iterations: maxIterations,
        temperature,
    };

    try {
        if (agentId) {
            await api(`/api/agents/${agentId}`, {
                method: 'PUT',
                body: JSON.stringify(data),
            });
            showToast('Agent updated successfully', 'success');
        } else {
            await api('/api/agents/', {
                method: 'POST',
                body: JSON.stringify(data),
            });
            showToast('Agent created successfully', 'success');
        }
        closeModal('agent-editor-modal');
        loadAgents();
    } catch (error) {
        showToast('Failed to save agent: ' + error.message, 'error');
    }
}

async function deleteAgent(agentId) {
    if (!confirm('Delete this agent? This cannot be undone.')) return;

    try {
        await api(`/api/agents/${agentId}`, { method: 'DELETE' });
        showToast('Agent deleted', 'success');
        loadAgents();
    } catch (error) {
        showToast('Failed to delete agent: ' + error.message, 'error');
    }
}

async function cloneTemplate(templateId, templateName) {
    const newName = prompt('Enter name for the new agent:', templateName + ' (Copy)');
    if (!newName) return;

    try {
        await api('/api/agents/clone', {
            method: 'POST',
            body: JSON.stringify({ template_id: templateId, new_name: newName }),
        });
        showToast('Template cloned successfully', 'success');
        loadAgents();
    } catch (error) {
        showToast('Failed to clone template: ' + error.message, 'error');
    }
}

async function runAgent() {
    const agentId = document.getElementById('agent-select').value;
    const prompt = document.getElementById('agent-prompt').value.trim();

    if (!agentId) {
        showToast('Please select an agent', 'warning');
        return;
    }
    if (!prompt) {
        showToast('Please enter a task description', 'warning');
        return;
    }
    if (!currentLibraryId) {
        showToast('Please select a library first', 'warning');
        return;
    }

    // Show execution log
    document.getElementById('agent-execution-log').classList.remove('hidden');
    document.getElementById('agent-approval-dialog').classList.add('hidden');
    document.getElementById('agent-log-content').innerHTML = '';
    document.getElementById('btn-run-agent').disabled = true;

    addAgentLog('info', 'Starting agent...');

    try {
        const response = await fetch(`/api/agents/${agentId}/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                library_id: currentLibraryId,
                prompt: prompt,
            }),
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Failed to start agent' }));
            throw new Error(error.detail);
        }

        // Get task ID from header
        currentAgentTaskId = response.headers.get('X-Task-ID');

        // Process SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const event = JSON.parse(line.slice(6));
                        handleAgentEvent(event);
                    } catch (e) {
                        console.warn('Failed to parse agent event:', e);
                    }
                }
            }
        }

    } catch (error) {
        addAgentLog('error', 'Error: ' + error.message);
    } finally {
        document.getElementById('btn-run-agent').disabled = false;
        currentAgentTaskId = null;
    }
}

function handleAgentEvent(event) {
    switch (event.type) {
        case 'started':
            addAgentLog('info', `Agent "${event.agent_name}" started (Task: ${event.task_id.slice(0, 8)}...)`);
            break;

        case 'thinking':
            addAgentLog('thinking', event.content);
            break;

        case 'tool_call':
            addAgentLog('tool', `Calling tool: ${event.tool}`);
            break;

        case 'approval_needed':
            showApprovalDialog(event.approval);
            break;

        case 'tool_approved':
            hideApprovalDialog();
            addAgentLog('info', `Tool approved: ${event.tool}`);
            break;

        case 'tool_rejected':
            hideApprovalDialog();
            addAgentLog('warning', `Tool rejected: ${event.tool} - ${event.reason}`);
            break;

        case 'tool_result':
            addAgentLog('result', `Tool result from ${event.tool}`);
            break;

        case 'response':
            addAgentLog('response', event.content);
            break;

        case 'complete':
            addAgentLog('success', `Task completed in ${event.iterations} iterations`);
            addAgentLog('result', event.result);
            break;

        case 'error':
            addAgentLog('error', event.message);
            break;
    }
}

function addAgentLog(type, content) {
    const logContent = document.getElementById('agent-log-content');
    const entry = document.createElement('div');
    entry.className = `log-entry log-${type}`;

    const time = new Date().toLocaleTimeString();
    entry.innerHTML = `
        <span class="log-time">[${time}]</span>
        <span class="log-message">${escapeHtml(content)}</span>
    `;

    logContent.appendChild(entry);
    logContent.scrollTop = logContent.scrollHeight;
}

function showApprovalDialog(approval) {
    currentApprovalId = approval.id;
    document.getElementById('approval-description').textContent = approval.description;
    document.getElementById('approval-tool').textContent = approval.tool;
    document.getElementById('approval-args').textContent = JSON.stringify(approval.args, null, 2);
    document.getElementById('agent-approval-dialog').classList.remove('hidden');
}

function hideApprovalDialog() {
    document.getElementById('agent-approval-dialog').classList.add('hidden');
    currentApprovalId = null;
}

async function submitApproval(approved) {
    if (!currentApprovalId) return;

    const reason = approved ? null : prompt('Reason for rejection (optional):');

    try {
        await api('/api/agents/approve', {
            method: 'POST',
            body: JSON.stringify({
                approval_id: currentApprovalId,
                approved: approved,
                reason: reason,
            }),
        });

        if (!approved) {
            addAgentLog('warning', 'Action rejected by user');
        }
    } catch (error) {
        addAgentLog('error', 'Failed to submit approval: ' + error.message);
    }

    hideApprovalDialog();
}

async function cancelAgentTask() {
    if (!currentAgentTaskId) return;

    try {
        await api(`/api/agents/tasks/${currentAgentTaskId}/cancel`, { method: 'POST' });
        addAgentLog('warning', 'Task cancelled');
    } catch (error) {
        addAgentLog('error', 'Failed to cancel task: ' + error.message);
    }
}

// Event Listeners
document.addEventListener('DOMContentLoaded', () => {
    // Initialize
    loadLibraries();

    // Tabs
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

            tab.classList.add('active');
            document.getElementById(`tab-${tab.dataset.tab}`).classList.add('active');
        });
    });

    // Markdown render toggle — restore persisted state
    const mdToggle = document.getElementById('chat-render-markdown');
    if (mdToggle) mdToggle.checked = markdownRenderEnabled;

    // Chat
    document.getElementById('btn-chat').addEventListener('click', sendChatMessage);
    document.getElementById('btn-chat-clear').addEventListener('click', clearChat);
    document.getElementById('chat-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendChatMessage();
        }
    });

    // Search
    document.getElementById('btn-search').addEventListener('click', performSearch);
    document.getElementById('search-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') performSearch();
    });

    // File upload
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');

        const files = Array.from(e.dataTransfer.files);
        files.forEach(uploadFile);
    });

    fileInput.addEventListener('change', () => {
        const files = Array.from(fileInput.files);
        files.forEach(uploadFile);
        fileInput.value = '';
    });

    // Text ingest
    document.getElementById('btn-ingest-text').addEventListener('click', ingestText);

    // Settings
    document.getElementById('btn-settings').addEventListener('click', () => {
        switchSettingsTab('api');
        loadSettings();
        openModal('settings-modal');
    });

    document.getElementById('btn-save-settings').addEventListener('click', saveSettings);
    document.getElementById('btn-test-connection').addEventListener('click', testConnection);

    // Library modal
    document.getElementById('btn-new-library').addEventListener('click', () => {
        document.getElementById('library-modal-title').textContent = 'New Library';
        document.getElementById('library-name').value = '';
        document.getElementById('library-description').value = '';
        openModal('library-modal');
    });

    document.getElementById('btn-save-library').addEventListener('click', createLibrary);

    // Close modals on outside click
    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.classList.add('hidden');
            }
        });
    });

    // Google Drive
    loadGoogleDriveStatus();

    document.getElementById('btn-upload-credentials').addEventListener('click', uploadGoogleCredentials);
    document.getElementById('btn-gdrive-connect').addEventListener('click', startGoogleAuth);
    document.getElementById('btn-gdrive-disconnect').addEventListener('click', disconnectGoogleDrive);
    document.getElementById('btn-gdrive-complete-auth').addEventListener('click', completeGoogleAuth);
    document.getElementById('btn-browse-gdrive').addEventListener('click', openGoogleDriveBrowser);
    document.getElementById('btn-gdrive-import').addEventListener('click', importSelectedGdriveFiles);

    // Google Drive export format
    document.getElementById('gdrive-export-format').addEventListener('change', async (e) => {
        try {
            await api('/api/settings', {
                method: 'PUT',
                body: JSON.stringify({
                    google_drive: { export_format: e.target.value }
                }),
            });
        } catch (error) {
            console.error('Failed to save export format:', error);
        }
    });

    // Agents
    loadAgents();

    document.getElementById('btn-new-agent').addEventListener('click', () => openAgentEditor());
    document.getElementById('btn-save-agent').addEventListener('click', saveAgent);
    document.getElementById('btn-run-agent').addEventListener('click', runAgent);
    document.getElementById('btn-cancel-agent').addEventListener('click', cancelAgentTask);
    document.getElementById('btn-approve').addEventListener('click', () => submitApproval(true));
    document.getElementById('btn-reject').addEventListener('click', () => submitApproval(false));

    // Enable/disable run button based on agent selection
    document.getElementById('agent-select').addEventListener('change', (e) => {
        document.getElementById('btn-run-agent').disabled = !e.target.value;
    });

    // Chat agent selector: show/hide auto-approve and info button
    document.getElementById('chat-agent-select').addEventListener('change', onChatAgentChanged);
    document.getElementById('btn-agent-info').addEventListener('click', showAgentInfo);
});

/**
 * Build HTML for graph entity chips shown below the chat answer.
 * @param {Array} entities  - array of {name, entity_type}
 * @param {string} libraryId
 * @returns {string} HTML string
 */
function buildGraphEntitiesHtml(entities, libraryId) {
    if (!entities || entities.length === 0) return '';
    const chips = entities.map(e => {
        const safeName = escapeHtml(e.name);
        const safeType = escapeHtml(e.entity_type);
        const dataName = e.name.replace(/'/g, "\\'");
        const dataLib  = (libraryId || '').replace(/'/g, "\\'");
        return `<button class="entity-chip"
                    title="${safeType}"
                    onclick="showEntityNeighbors('${dataName}', '${dataLib}')">
                    ${safeName}<span class="entity-chip-type">${safeType}</span>
                </button>`;
    }).join('');
    return `<div class="message-entities">
                <strong>Graph Entities:</strong>
                <div class="entity-chips">${chips}</div>
            </div>`;
}

/**
 * Fetch and display the neighbors of an entity in the entity modal.
 * @param {string} entityName
 * @param {string} libraryId
 */
async function showEntityNeighbors(entityName, libraryId) {
    const modalTitle   = document.getElementById('entity-modal-title');
    const modalType    = document.getElementById('entity-modal-type');
    const modalLoading = document.getElementById('entity-modal-loading');
    const modalContent = document.getElementById('entity-modal-content');

    modalTitle.textContent   = entityName;
    modalType.textContent    = '';
    modalContent.innerHTML   = '';
    modalLoading.classList.remove('hidden');
    openModal('entity-modal');

    try {
        const encodedLib  = encodeURIComponent(libraryId);
        const encodedName = encodeURIComponent(entityName);
        const data = await api(`/api/graph/entity/${encodedLib}/${encodedName}/neighbors`);

        modalTitle.textContent = data.entity_name;
        modalType.textContent  = data.entity_type;

        let content = '';
        if (!data.relationships || data.relationships.length === 0) {
            content = '<p class="text-muted" style="padding:0.5rem 0">No direct relationships found for this entity.</p>';
        } else {
            const rows = data.relationships.map(rel => {
                let relLabel = rel.relationship_type;
                let fromName = data.entity_name;
                let toName   = rel.target_name;
                if (relLabel.startsWith('inverse_')) {
                    relLabel = relLabel.slice(8);   // strip 'inverse_'
                    [fromName, toName] = [toName, fromName];
                }
                return `<div class="entity-rel-row">
                    <span class="entity-rel-name">${escapeHtml(fromName)}</span>
                    <span class="entity-rel-type">${escapeHtml(relLabel)}</span>
                    <span class="entity-rel-name">${escapeHtml(toName)}</span>
                    <span class="entity-rel-target-type">(${escapeHtml(rel.target_type)})</span>
                </div>`;
            }).join('');
            content = `<div class="entity-rel-list">${rows}</div>`;
        }

        modalLoading.classList.add('hidden');
        modalContent.innerHTML = content;
    } catch (error) {
        modalLoading.classList.add('hidden');
        modalContent.innerHTML = `<p class="text-muted" style="padding:0.5rem 0">Failed to load relationships: ${escapeHtml(error.message)}</p>`;
    }
}

// Make functions available globally
window.closeModal = closeModal;
window.showSourceDetails = showSourceDetails;
window.clearLibraryVectors = clearLibraryVectors;
window.clearLibraryGraphs = clearLibraryGraphs;
window.deleteCurrentLibrary = deleteCurrentLibrary;
window.loadGoogleDriveFolder = loadGoogleDriveFolder;
window.toggleGdriveFile = toggleGdriveFile;
window.editAgent = editAgent;
window.deleteAgent = deleteAgent;
window.cloneTemplate = cloneTemplate;
window.submitChatApproval = submitChatApproval;
window.showEntityNeighbors = showEntityNeighbors;

// =============================================================================
// Console / Log Stream
// =============================================================================

let consoleEventSource = null;
let consoleOpen = false;

function toggleConsole() {
    consoleOpen = !consoleOpen;
    const panel = document.getElementById('console-panel');
    const icon = document.getElementById('console-toggle-icon');
    if (consoleOpen) {
        panel.classList.remove('hidden');
        if (icon) icon.textContent = '\u25BC';
        startConsoleStream();
    } else {
        panel.classList.add('hidden');
        if (icon) icon.textContent = '\u25B2';
        stopConsoleStream();
    }
}

function startConsoleStream() {
    if (consoleEventSource) return;
    consoleEventSource = new EventSource('/api/logs/stream');
    consoleEventSource.onmessage = (event) => {
        appendConsoleLine(event.data);
    };
    consoleEventSource.onerror = () => {
        appendConsoleLine('[Console: connection lost]');
        stopConsoleStream();
    };
}

function stopConsoleStream() {
    if (consoleEventSource) {
        consoleEventSource.close();
        consoleEventSource = null;
    }
}

function appendConsoleLine(text) {
    const output = document.getElementById('console-output');
    if (!output) return;
    const line = document.createElement('div');
    line.className = 'console-line';
    if (text.includes(' ERROR ') || text.includes(' CRITICAL ')) {
        line.classList.add('console-error');
    } else if (text.includes(' WARNING ')) {
        line.classList.add('console-warn');
    } else if (text.includes(' DEBUG ')) {
        line.classList.add('console-debug');
    }
    line.textContent = text;
    output.appendChild(line);
    output.scrollTop = output.scrollHeight;
    // Cap at 500 lines to prevent memory growth
    while (output.children.length > 500) {
        output.removeChild(output.firstChild);
    }
}

function clearConsole() {
    const output = document.getElementById('console-output');
    if (output) output.innerHTML = '';
}

window.toggleConsole = toggleConsole;
window.clearConsole = clearConsole;
