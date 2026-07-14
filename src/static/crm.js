(() => {
  'use strict';

  const categoryDefs = [
    {id:'construction', label:'Construction & Building', icon:'🏗️', keywords:['construction','contractor','builder','remodel','architecture','architect','engineering','demolition','steel','concrete','masonry','roof','painting','glazing','elevator','reinforcing']},
    {id:'electrical', label:'Electrical & Technology', icon:'⚡', keywords:['electric','electrical','lighting','low voltage','technology','tech','software','systems','telecom','radio','security','automation']},
    {id:'mechanical', label:'Mechanical, Plumbing & Fire', icon:'🔧', keywords:['mechanical','plumbing','plumber','hvac','heating','cooling','piping','sprinkler','fire protection','air conditioning']},
    {id:'site', label:'Site Work & Landscaping', icon:'🌿', keywords:['landscape','landscaping','nursery','erosion','excavation','earthwork','site work','sitework','paving','gravel','bobcat','grounds','lawn']},
    {id:'logistics', label:'Logistics & Transportation', icon:'🚚', keywords:['trucking','transport','transportation','logistics','courier','hauling','moving','delivery','fleet']},
    {id:'facilities', label:'Cleaning & Facilities', icon:'🧹', keywords:['cleaning','janitorial','maintenance','facility','facilities','waste','compost','sanitation']},
    {id:'professional', label:'Professional & Consulting', icon:'💼', keywords:['consulting','accounting','law','legal','workforce','staffing','training','education','planning','insurance','finance','procurement','management','real estate']},
    {id:'creative', label:'Creative, Marketing & Events', icon:'🎨', keywords:['marketing','design','photography','event','apparel','printing','promotion','sign','media','advertising','gallery','festival','art']},
    {id:'health', label:'Health & Human Services', icon:'🩺', keywords:['health','healthcare','nursing','home care','medical','dental','wellness','beauty','childcare','community service']},
    {id:'supplier', label:'Suppliers & Products', icon:'📦', keywords:['supply','supplier','products','manufacturing','furniture','office','equipment','materials','distribution','wholesale']},
    {id:'other', label:'Other / Unclassified', icon:'✦', keywords:[]}
  ];

  const statusLabels = {
    matched:'Matched',
    pending:'Pending Review',
    support:'Needs Support',
    lost:'No Match'
  };

  const state = {
    firms: [],
    opportunities: [],
    status: 'all',
    category: 'all',
    mode: 'heart',
    cardLimit: 60,
    opportunityLimit: 40,
    heartScale: 1,
    heartX: -380,
    heartY: -310,
    dragging: false,
    dragStart: {x:0,y:0}
  };

  const $ = id => document.getElementById(id);
  const heartStage = $('heartStage');
  const heartView = $('heartView');
  const clusterView = $('clusterView');
  const cardsView = $('cardsView');
  const opportunitiesView = $('opportunitiesView');
  const tooltip = $('tooltip');
  const detailPanel = $('detailPanel');
  const searchInput = $('searchInput');

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function categoryFor(firm) {
    if (firm.category_id) {
      return categoryDefs.find(item => item.id === firm.category_id) || categoryDefs.at(-1);
    }
    const haystack = [firm.trade, firm.name, firm.cap, firm.capabilities, firm.notes]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return categoryDefs.find(item => item.id !== 'other' && item.keywords.some(keyword => haystack.includes(keyword)))
      || categoryDefs.at(-1);
  }

  function queryText() {
    return searchInput.value.trim().toLowerCase();
  }

  function firmMatchesSearch(firm, includeCategory = true) {
    const query = queryText();
    if (!query) return true;
    const category = categoryFor(firm);
    const text = [
      firm.name,
      firm.contact,
      firm.trade,
      firm.cap,
      firm.capabilities,
      firm.notes,
      ...(firm.certs || []),
      includeCategory ? category.label : ''
    ].filter(Boolean).join(' ').toLowerCase();
    return text.includes(query);
  }

  function filteredFirms({ignoreCategory = false} = {}) {
    return state.firms.filter(firm => {
      if (state.status !== 'all' && firm.status !== state.status) return false;
      if (!ignoreCategory && state.category !== 'all' && categoryFor(firm).id !== state.category) return false;
      return firmMatchesSearch(firm);
    });
  }

  function filteredOpportunities() {
    const query = queryText();
    if (!query) return state.opportunities;
    return state.opportunities.filter(item => [
      item.scope_number,
      item.title,
      item.description,
      item.phase,
      item.status,
      item.price_range,
      ...(item.categories || [])
    ].filter(Boolean).join(' ').toLowerCase().includes(query));
  }

  function setNotice(message, type = 'success') {
    const notice = $('dataNotice');
    notice.textContent = message;
    notice.className = `data-notice ${type}`;
    if (type === 'success') {
      window.setTimeout(() => {
        if (notice.textContent === message) notice.className = 'data-notice';
      }, 4500);
    }
  }

  function renderStats() {
    $('statTotal').textContent = state.firms.length.toLocaleString();
    $('statMatched').textContent = state.firms.filter(item => item.status === 'matched').length.toLocaleString();
    $('statPending').textContent = state.firms.filter(item => item.status === 'pending').length.toLocaleString();
    $('statSupport').textContent = state.firms.filter(item => item.status === 'support').length.toLocaleString();
    $('statLost').textContent = state.firms.filter(item => item.status === 'lost').length.toLocaleString();
    $('statOpportunities').textContent = state.opportunities.length.toLocaleString();
  }

  function renderStatusFilters() {
    const container = $('statusFilters');
    container.innerHTML = '';
    ['all','matched','pending','support','lost'].forEach(key => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `status-button${state.status === key ? ' active' : ''}`;
      button.textContent = key === 'all' ? 'All' : statusLabels[key];
      button.addEventListener('click', () => {
        state.status = key;
        state.cardLimit = 60;
        renderStatusFilters();
        renderCategoryFilters();
        renderCurrentView();
      });
      container.appendChild(button);
    });
  }

  function renderCategoryFilters() {
    const container = $('categoryFilters');
    container.innerHTML = '';
    const base = filteredFirms({ignoreCategory:true});
    const counts = new Map(categoryDefs.map(item => [item.id, 0]));
    base.forEach(firm => counts.set(categoryFor(firm).id, counts.get(categoryFor(firm).id) + 1));

    const allButton = document.createElement('button');
    allButton.type = 'button';
    allButton.className = `category-button${state.category === 'all' ? ' active' : ''}`;
    allButton.textContent = `All categories (${base.length})`;
    allButton.addEventListener('click', () => {
      state.category = 'all';
      state.cardLimit = 60;
      renderCategoryFilters();
      renderCards();
    });
    container.appendChild(allButton);

    categoryDefs.forEach(category => {
      const count = counts.get(category.id);
      if (!count) return;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `category-button${state.category === category.id ? ' active' : ''}`;
      button.textContent = `${category.icon} ${category.label} (${count})`;
      button.addEventListener('click', () => {
        state.category = category.id;
        state.cardLimit = 60;
        renderCategoryFilters();
        renderCards();
      });
      container.appendChild(button);
    });
  }

  function heartPoints(count, width, height) {
    if (count <= 0) return [];
    if (count === 1) return [{x:width/2,y:height/2}];
    let columns = Math.max(28, Math.ceil(Math.sqrt(count * 2.7)));
    let rows = Math.ceil(columns * .92);
    let candidates = [];
    while (true) {
      candidates = [];
      for (let row = 0; row < rows; row += 1) {
        const y = 1.35 - (row / (rows - 1)) * 2.7;
        for (let column = 0; column < columns; column += 1) {
          const x = -1.4 + (column / (columns - 1)) * 2.8;
          const equation = Math.pow(x*x + y*y - 1, 3) - x*x*Math.pow(y, 3);
          if (equation <= 0) {
            candidates.push({
              x: width/2 + x * width * .31,
              y: height/2 - y * height * .33
            });
          }
        }
      }
      if (candidates.length >= count || columns >= 190) break;
      columns += 4;
      rows = Math.ceil(columns * .92);
    }
    if (candidates.length <= count) return candidates.slice(0, count);
    const chosen = [];
    const stride = (candidates.length - 1) / (count - 1);
    for (let index = 0; index < count; index += 1) chosen.push(candidates[Math.round(index * stride)]);
    return chosen;
  }

  function heartPath(width, height) {
    const points = [];
    for (let index = 0; index <= 160; index += 1) {
      const t = (index / 160) * Math.PI * 2;
      const x = 16 * Math.pow(Math.sin(t), 3);
      const y = 13 * Math.cos(t) - 5 * Math.cos(2*t) - 2 * Math.cos(3*t) - Math.cos(4*t);
      points.push({x:width/2 + x*width/38, y:height*.48 - y*height/33});
    }
    return points.map((point,index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' ') + ' Z';
  }

  function updateHeartTransform() {
    heartStage.style.transform = `translate(${state.heartX}px, ${state.heartY}px) scale(${state.heartScale})`;
  }

  function renderHeart() {
    heartStage.querySelectorAll('.heart-node').forEach(node => node.remove());
    const firms = filteredFirms();
    const points = heartPoints(firms.length, 760, 620);
    const size = Math.max(9, Math.min(62, 460 / Math.sqrt(Math.max(firms.length, 1))));
    firms.forEach((firm,index) => {
      const node = document.createElement('button');
      node.type = 'button';
      node.className = `heart-node ${firm.status}`;
      node.style.left = `${points[index].x}px`;
      node.style.top = `${points[index].y}px`;
      node.style.width = `${size}px`;
      node.style.height = `${size}px`;
      node.setAttribute('aria-label', firm.name);
      node.title = firm.name;
      node.addEventListener('mouseenter', event => showFirmTooltip(event, firm));
      node.addEventListener('mousemove', moveTooltip);
      node.addEventListener('mouseleave', hideTooltip);
      node.addEventListener('click', () => openFirmDetail(firm));
      heartStage.appendChild(node);
    });
  }

  function renderClusters() {
    const grid = $('clusterGrid');
    grid.innerHTML = '';
    const grouped = new Map(categoryDefs.map(item => [item.id, []]));
    filteredFirms().forEach(firm => grouped.get(categoryFor(firm).id).push(firm));

    categoryDefs.forEach(category => {
      const members = grouped.get(category.id);
      if (!members.length) return;
      const card = document.createElement('article');
      card.className = 'cluster-card';
      card.innerHTML = `<div class="cluster-head"><div class="cluster-title"><span class="cluster-icon">${category.icon}</span><h3>${escapeHtml(category.label)}</h3></div><span class="cluster-count">${members.length} firms</span></div>`;

      const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
      svg.setAttribute('viewBox','0 0 300 210');
      svg.setAttribute('class','cluster-svg');
      const outline = document.createElementNS('http://www.w3.org/2000/svg','path');
      outline.setAttribute('d', heartPath(300,210));
      outline.setAttribute('class','cluster-heart-outline');
      svg.appendChild(outline);

      const points = heartPoints(members.length,300,210);
      const radius = Math.max(2, Math.min(7, 40 / Math.sqrt(Math.max(members.length,1))));
      members.forEach((firm,index) => {
        const dot = document.createElementNS('http://www.w3.org/2000/svg','circle');
        dot.setAttribute('cx',points[index].x);
        dot.setAttribute('cy',points[index].y);
        dot.setAttribute('r',radius);
        dot.setAttribute('fill',`var(--${firm.status === 'matched' ? 'green' : firm.status === 'pending' ? 'orange' : firm.status === 'support' ? 'blue' : 'red'})`);
        dot.setAttribute('class','cluster-dot');
        dot.setAttribute('tabindex','0');
        dot.addEventListener('mouseenter', event => {
          dot.setAttribute('r',radius*1.75);
          showFirmTooltip(event,firm);
        });
        dot.addEventListener('mousemove',moveTooltip);
        dot.addEventListener('mouseleave',() => {
          dot.setAttribute('r',radius);
          hideTooltip();
        });
        dot.addEventListener('click',() => openFirmDetail(firm));
        svg.appendChild(dot);
      });
      card.appendChild(svg);
      grid.appendChild(card);
    });

    if (!grid.children.length) grid.innerHTML = '<div class="empty-state">No category clusters match the current filters.</div>';
  }

  function renderCards() {
    renderCategoryFilters();
    const grid = $('cardsGrid');
    const loadWrap = $('cardLoadMore');
    grid.innerHTML = '';
    loadWrap.innerHTML = '';
    const firms = filteredFirms().sort((a,b) => a.name.localeCompare(b.name));
    const shown = firms.slice(0,state.cardLimit);

    shown.forEach(firm => {
      const category = categoryFor(firm);
      const card = document.createElement('article');
      card.className = 'firm-card';
      card.innerHTML = `
        <div class="card-head">
          <div class="logo ${firm.status}">${escapeHtml(firm.logo || '')}</div>
          <div><h3>${escapeHtml(firm.name)}</h3><p>${escapeHtml(firm.contact || 'No contact listed')}</p></div>
        </div>
        <div class="tag-row">
          <span class="category-tag">${category.icon} ${escapeHtml(category.label)}</span>
          <span class="badge ${firm.status}">${statusLabels[firm.status] || 'Pending Review'}</span>
          ${firm.priority ? '<span class="badge pending">Priority</span>' : ''}
        </div>
        <dl>
          <dt>Trade</dt><dd>${escapeHtml(firm.trade || 'Other')}</dd>
          <dt>Certifications</dt><dd>${escapeHtml((firm.certs || []).join(', ') || 'None listed')}</dd>
          <dt>Match</dt><dd>${escapeHtml(firm.match || 'No active matched opportunity yet')}</dd>
        </dl>`;
      card.addEventListener('click',() => openFirmDetail(firm));
      grid.appendChild(card);
    });

    if (!shown.length) grid.innerHTML = '<div class="empty-state">No firms match the current status, category, and search filters.</div>';
    if (shown.length < firms.length) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'load-more';
      button.textContent = `Load 60 more (${firms.length - shown.length} remaining)`;
      button.addEventListener('click',() => {
        state.cardLimit += 60;
        renderCards();
      });
      loadWrap.appendChild(button);
    }
  }

  function renderOpportunities() {
    const grid = $('opportunitiesGrid');
    const loadWrap = $('opportunityLoadMore');
    grid.innerHTML = '';
    loadWrap.innerHTML = '';
    const opportunities = filteredOpportunities();
    const shown = opportunities.slice(0,state.opportunityLimit);

    shown.forEach(item => {
      const card = document.createElement('article');
      card.className = 'opportunity-card';
      card.innerHTML = `
        <h3>${escapeHtml(item.title || item.scope_number || 'Untitled Opportunity')}</h3>
        <p>${escapeHtml(item.scope_number || 'No scope number')} • ${escapeHtml(item.phase || 'Phase not listed')}</p>
        <div class="tag-row">
          ${(item.categories || []).map(category => `<span class="category-tag">${escapeHtml(category)}</span>`).join('')}
          ${item.status ? `<span class="badge">${escapeHtml(item.status)}</span>` : ''}
        </div>
        <dl>
          <dt>Due</dt><dd>${escapeHtml(item.deadline || 'Not listed')}</dd>
          <dt>Price</dt><dd>${escapeHtml(item.price_range || 'Not listed')}</dd>
          <dt>Matches</dt><dd>${Number(item.match_count || 0).toLocaleString()} firms</dd>
        </dl>`;
      card.addEventListener('click',() => openOpportunityDetail(item));
      grid.appendChild(card);
    });

    if (!shown.length) grid.innerHTML = '<div class="empty-state">No opportunities are currently available from Airtable, or none match the search.</div>';
    if (shown.length < opportunities.length) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'load-more';
      button.textContent = `Load 40 more (${opportunities.length - shown.length} remaining)`;
      button.addEventListener('click',() => {
        state.opportunityLimit += 40;
        renderOpportunities();
      });
      loadWrap.appendChild(button);
    }
  }

  function showFirmTooltip(event,firm) {
    const category = categoryFor(firm);
    tooltip.innerHTML = `<h4>${escapeHtml(firm.name)}</h4><p>${category.icon} ${escapeHtml(category.label)}</p><p>${escapeHtml(firm.trade || 'Other')} • <strong>${statusLabels[firm.status] || 'Pending Review'}</strong></p>`;
    tooltip.classList.add('show');
    moveTooltip(event);
  }

  function moveTooltip(event) {
    tooltip.style.left = `${Math.min(window.innerWidth - 280,event.clientX + 15)}px`;
    tooltip.style.top = `${Math.min(window.innerHeight - 130,event.clientY + 15)}px`;
  }

  function hideTooltip() { tooltip.classList.remove('show'); }

  function openFirmDetail(firm) {
    const category = categoryFor(firm);
    detailPanel.innerHTML = `
      <h3>${escapeHtml(firm.name)}</h3>
      <p class="detail-sub">${escapeHtml(firm.trade || 'Other')} • ${escapeHtml(firm.contact || 'No contact listed')}</p>
      <div class="tag-row"><span class="category-tag">${category.icon} ${escapeHtml(category.label)}</span><span class="badge ${firm.status}">${statusLabels[firm.status] || 'Pending Review'}</span></div>
      <section class="detail-section"><h4>Business Details</h4><div class="detail-grid">
        <strong>Email</strong><span>${escapeHtml(firm.email || 'Not listed')}</span>
        <strong>Phone</strong><span>${escapeHtml(firm.phone || 'Not listed')}</span>
        <strong>Website</strong><span>${escapeHtml(firm.website || 'Not listed')}</span>
        <strong>Address</strong><span>${escapeHtml(firm.address || 'Not listed')}</span>
        <strong>Certifications</strong><span>${escapeHtml((firm.certs || []).join(', ') || 'None listed')}</span>
        <strong>Ready to Bid</strong><span>${escapeHtml(firm.ready || 'Unknown')}</span>
        <strong>Needs Support</strong><span>${escapeHtml(firm.support || 'Unknown')}</span>
      </div></section>
      <section class="detail-section"><h4>Capabilities</h4><p>${escapeHtml(firm.cap || 'No capabilities listed yet.')}</p></section>
      <section class="detail-section"><h4>Match Information</h4><p><strong>${escapeHtml(firm.match || 'No active matched opportunity yet')}</strong></p><p>${escapeHtml(firm.reason || '')}</p></section>
      ${firm.notes ? `<section class="detail-section"><h4>Notes</h4><p>${escapeHtml(firm.notes)}</p></section>` : ''}`;
  }

  function openOpportunityDetail(item) {
    detailPanel.innerHTML = `
      <h3>${escapeHtml(item.title || item.scope_number || 'Opportunity')}</h3>
      <p class="detail-sub">${escapeHtml(item.scope_number || '')} • ${escapeHtml(item.phase || 'Phase not listed')}</p>
      <div class="tag-row">${(item.categories || []).map(category => `<span class="category-tag">${escapeHtml(category)}</span>`).join('')}${item.status ? `<span class="badge">${escapeHtml(item.status)}</span>` : ''}</div>
      <section class="detail-section"><h4>Opportunity Details</h4><div class="detail-grid">
        <strong>Price Range</strong><span>${escapeHtml(item.price_range || 'Not listed')}</span>
        <strong>Release Date</strong><span>${escapeHtml(item.release_for_bid || 'Not listed')}</span>
        <strong>Quotes Due</strong><span>${escapeHtml(item.deadline || 'Not listed')}</span>
        <strong>Matched Firms</strong><span>${Number(item.match_count || 0).toLocaleString()}</span>
        <strong>Last Scraped</strong><span>${escapeHtml(item.last_scraped || 'Not listed')}</span>
      </div></section>
      <section class="detail-section"><h4>Scope Description</h4><p>${escapeHtml(item.description || 'No scope description listed.')}</p></section>
      ${item.source_url ? `<section class="detail-section"><h4>Source</h4><p><a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener">Open source opportunity page</a></p></section>` : ''}`;
  }

  function renderCurrentView() {
    if (state.mode === 'heart') renderHeart();
    if (state.mode === 'clusters') renderClusters();
    if (state.mode === 'cards') renderCards();
    if (state.mode === 'opportunities') renderOpportunities();
  }

  function updateViewButtons() {
    document.querySelectorAll('[data-mode]').forEach(button => button.classList.toggle('active',button.dataset.mode === state.mode));
  }

  function setMode(mode) {
    state.mode = mode;
    heartView.classList.toggle('active',mode === 'heart');
    clusterView.classList.toggle('active',mode === 'clusters');
    cardsView.classList.toggle('active',mode === 'cards');
    opportunitiesView.classList.toggle('active',mode === 'opportunities');
    $('statusFilters').style.display = mode === 'opportunities' ? 'none' : 'flex';
    document.querySelector('.view-switcher').style.display = mode === 'opportunities' ? 'none' : 'flex';
    updateViewButtons();
    renderCurrentView();
  }

  function activateSidebar(page) {
    document.querySelectorAll('[data-page]').forEach(button => button.classList.toggle('active',button.dataset.page === page));
    if (page === 'crm') {
      state.status = 'all';
      state.category = 'all';
      $('pageTitle').textContent = 'Michelle CRM View';
      $('pageSubtitle').textContent = 'Explore the full network, category heart islands, or professional client cards.';
      setMode('heart');
    } else if (page === 'firms') {
      state.status = 'all';
      state.category = 'all';
      $('pageTitle').textContent = 'Firm Directory';
      $('pageSubtitle').textContent = 'Browse, search, and filter the complete live Airtable firm directory.';
      setMode('cards');
    } else if (page === 'matches') {
      state.status = 'matched';
      state.category = 'all';
      $('pageTitle').textContent = 'Matched Firms';
      $('pageSubtitle').textContent = 'Review firms currently marked as matched to an opportunity.';
      setMode('cards');
    } else if (page === 'opportunities') {
      $('pageTitle').textContent = 'Bid Opportunities';
      $('pageSubtitle').textContent = 'Review live Walsh Kokosing opportunities synchronized into Airtable.';
      setMode('opportunities');
    } else if (page === 'support') {
      state.status = 'support';
      state.category = 'all';
      $('pageTitle').textContent = 'Firms Needing Support';
      $('pageSubtitle').textContent = 'Focus on firms that need readiness assistance, follow-up, or resources.';
      setMode('cards');
    }
    state.cardLimit = 60;
    renderStatusFilters();
    renderCategoryFilters();
    window.location.hash = page;
  }

  async function fetchJson(url) {
    const response = await fetch(url,{headers:{Accept:'application/json'}});
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || `Request failed: ${response.status}`);
    return payload;
  }

  async function loadData({refreshOpportunities = false} = {}) {
    setNotice('Connecting to Airtable…','loading');
    try {
      const [firmsPayload,opportunitiesPayload] = await Promise.all([
        fetchJson('/api/firms'),
        fetchJson(`/api/opportunities${refreshOpportunities ? '?refresh=1' : ''}`)
      ]);
      state.firms = Array.isArray(firmsPayload.firms) ? firmsPayload.firms : [];
      state.opportunities = Array.isArray(opportunitiesPayload.opportunities) ? opportunitiesPayload.opportunities : [];
      renderStats();
      renderStatusFilters();
      renderCategoryFilters();
      renderCurrentView();
      setNotice(`Live Airtable sync loaded ${state.firms.length.toLocaleString()} firms and ${state.opportunities.length.toLocaleString()} opportunities.`,'success');
    } catch (error) {
      console.error(error);
      state.firms = [];
      state.opportunities = [];
      renderStats();
      renderCurrentView();
      setNotice(`Live Airtable data could not load: ${error.message}`,'error');
    }
  }

  document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click',() => setMode(button.dataset.mode)));
  document.querySelectorAll('[data-page]').forEach(button => button.addEventListener('click',() => activateSidebar(button.dataset.page)));
  $('clearCategory').addEventListener('click',() => {
    state.category = 'all';
    state.cardLimit = 60;
    renderCategoryFilters();
    renderCards();
  });
  $('refreshOpportunities').addEventListener('click',() => loadData({refreshOpportunities:true}));
  searchInput.addEventListener('input',() => {
    state.cardLimit = 60;
    state.opportunityLimit = 40;
    renderCategoryFilters();
    renderCurrentView();
  });

  heartView.addEventListener('wheel',event => {
    event.preventDefault();
    state.heartScale = Math.min(2.5,Math.max(.5,state.heartScale + (event.deltaY > 0 ? -.08 : .08)));
    updateHeartTransform();
  },{passive:false});
  heartView.addEventListener('mousedown',event => {
    state.dragging = true;
    state.dragStart = {x:event.clientX - state.heartX,y:event.clientY - state.heartY};
  });
  window.addEventListener('mousemove',event => {
    if (!state.dragging) return;
    state.heartX = event.clientX - state.dragStart.x;
    state.heartY = event.clientY - state.dragStart.y;
    updateHeartTransform();
  });
  window.addEventListener('mouseup',() => { state.dragging = false; });

  renderStatusFilters();
  renderCategoryFilters();
  updateHeartTransform();
  const initialPage = ['crm','firms','matches','opportunities','support'].includes(window.location.hash.slice(1))
    ? window.location.hash.slice(1)
    : 'crm';
  activateSidebar(initialPage);
  loadData();
})();
