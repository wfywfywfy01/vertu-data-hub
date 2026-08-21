const dealerSelect = document.querySelector("#dealer");
const dealerMeta = document.querySelector("#dealer-meta");
const categorySelect = document.querySelector("#category");
const form = document.querySelector("#search-form");
const queryInput = document.querySelector("#query");
const searchButton = document.querySelector("#search-button");
const notice = document.querySelector("#notice");
const resultsSection = document.querySelector("#results-section");
const resultsList = document.querySelector("#results");
const resultCount = document.querySelector("#result-count");
const template = document.querySelector("#result-template");

const categoryLabels = {
  dealer_profile: "经销商档案",
  contract_compliance: "合同与合规",
  store_display: "门店陈列",
  product_policy: "产品与政策",
  sales_inventory: "销售与库存",
  marketing_training: "市场与培训",
  communications: "沟通记录",
  logistics_after_sales: "物流与售后",
  finance_settlement: "财务结算",
  media: "图片与媒体",
  unclassified: "未分类",
};

const sensitivityLabels = {
  internal: "内部",
  confidential: "保密",
  restricted: "受限",
};

function setNotice(message, state = "") {
  notice.textContent = message;
  notice.className = `notice ${state}`.trim();
}

function setSearching(active) {
  searchButton.disabled = active || !dealerSelect.value;
  searchButton.textContent = active ? "检索中" : "查询";
  queryInput.setAttribute("aria-busy", String(active));
}

function formatScore(row) {
  if (row.lexical_score !== null) return "关键词命中";
  if (row.semantic_similarity !== null) {
    const percentage = Math.round(row.semantic_similarity * 100);
    return `${percentage < 35 ? "低相关" : "语义相似"} ${percentage}%`;
  }
  return "相关结果";
}

function excerpt(text, maximum = 360) {
  const value = (text || "").trim();
  if (!value) return "该图片未识别到可显示文字。";
  return value.length > maximum ? `${value.slice(0, maximum)}...` : value;
}

function renderResults(items) {
  resultsList.replaceChildren();
  items.forEach((row, index) => {
    const fragment = template.content.cloneNode(true);
    const citation = row.citation || {};
    fragment.querySelector(".result-index").textContent = String(index + 1).padStart(2, "0");
    fragment.querySelector(".result-title").textContent = citation.title || citation.original_name || "未命名资料";
    const score = fragment.querySelector(".result-score");
    score.textContent = formatScore(row);
    if (row.lexical_score === null && row.semantic_similarity < 0.35) score.classList.add("low");
    fragment.querySelector(".result-text").textContent = excerpt(row.text);
    fragment.querySelector(".citation-file").textContent = citation.original_name || "-";
    fragment.querySelector(".citation-version").textContent = `v${citation.version_number || 1}`;
    fragment.querySelector(".citation-category").textContent = categoryLabels[row.category] || row.category;
    fragment.querySelector(".citation-sensitivity").textContent = sensitivityLabels[row.sensitivity] || row.sensitivity;
    resultsList.append(fragment);
  });
  resultCount.textContent = `${items.length} 条相关片段`;
  resultsSection.hidden = false;
}

async function loadDealers() {
  try {
    const response = await fetch("/ui/api/dealers");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "经销商主表读取失败");
    dealerSelect.replaceChildren();
    data.items.forEach((dealer) => {
      const option = document.createElement("option");
      option.value = dealer.id;
      option.textContent = dealer.official_name;
      option.dataset.assets = dealer.asset_count;
      dealerSelect.append(option);
    });
    if (!data.items.length) {
      const option = document.createElement("option");
      option.textContent = "暂无已确认经销商";
      dealerSelect.append(option);
      setNotice("暂无可查询经销商，请先确认经销商主表。", "error");
      return;
    }
    dealerSelect.disabled = false;
    searchButton.disabled = false;
    const vmg = data.items.find((dealer) => dealer.official_name.startsWith("VMG"));
    if (vmg) dealerSelect.value = vmg.id;
    updateDealerMeta();
    queryInput.focus();
  } catch (error) {
    dealerSelect.replaceChildren(new Option("连接失败"));
    dealerMeta.textContent = "数据库连接异常";
    setNotice(error.message, "error");
  }
}

function updateDealerMeta() {
  const option = dealerSelect.selectedOptions[0];
  const count = Number(option?.dataset.assets || 0);
  dealerMeta.textContent = `${count} 项可检索资产`;
}

dealerSelect.addEventListener("change", updateDealerMeta);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query || !dealerSelect.value) return;

  setSearching(true);
  resultsSection.hidden = true;
  setNotice("正在检索已处理资料并生成引用...", "loading");
  try {
    const response = await fetch("/ui/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        dealer_id: dealerSelect.value,
        category: categorySelect.value || null,
        top_k: 8,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "检索失败");
    if (!data.items.length) {
      setNotice("没有找到可靠证据。可缩短问题，或改为全部资料后重试。", "error");
      return;
    }
    renderResults(data.items);
    setNotice(`已在 ${data.dealer.official_name} 范围内完成检索。`);
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setSearching(false);
  }
});

loadDealers();
