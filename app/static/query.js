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

const browserImagePattern = /\.(?:jpe?g|png|webp)$/i;

function setNotice(message, state = "") {
  notice.textContent = message;
  notice.className = `notice ${state}`.trim();
}

function errorMessage(error) {
  return error instanceof TypeError
    ? "本地知识库服务已停止，请重新启动服务后刷新页面。"
    : error.message;
}

function setSearching(active) {
  searchButton.disabled = active || !dealerSelect.value;
  searchButton.textContent = active ? "检索中" : "查询";
  queryInput.setAttribute("aria-busy", String(active));
}

function formatScore(row) {
  if (row.retrieval_kind === "image_semantic") {
    return `画面匹配 ${Math.round(row.semantic_similarity * 100)}%`;
  }
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

function formatTime(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(value / 60);
  return `${String(minutes).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

function renderResults(items) {
  resultsList.replaceChildren();
  items.forEach((row, index) => {
    const fragment = template.content.cloneNode(true);
    const citation = row.citation || {};
    const item = fragment.querySelector(".result-item");
    const preview = fragment.querySelector(".result-preview");
    const image = fragment.querySelector(".result-image");
    const previewUrl = `/ui/api/assets/${encodeURIComponent(row.asset_id)}/content`;
    fragment.querySelector(".result-index").textContent = String(index + 1).padStart(2, "0");
    fragment.querySelector(".result-title").textContent = citation.title || citation.original_name || "未命名资料";
    const score = fragment.querySelector(".result-score");
    score.textContent = formatScore(row);
    if (row.retrieval_kind !== "image_semantic" && row.lexical_score === null && row.semantic_similarity < 0.35) {
      score.classList.add("low");
    }
    fragment.querySelector(".result-text").textContent = excerpt(row.text);
    const caption = fragment.querySelector(".result-caption");
    if (row.suggested_caption) {
      caption.hidden = false;
      caption.querySelector("p").textContent = row.suggested_caption;
    }
    fragment.querySelector(".citation-file").textContent = citation.original_name || "-";
    fragment.querySelector(".citation-version").textContent = `v${citation.version_number || 1}`;
    fragment.querySelector(".citation-category").textContent = categoryLabels[row.category] || row.category;
    fragment.querySelector(".citation-sensitivity").textContent = sensitivityLabels[row.sensitivity] || row.sensitivity;
    if (citation.timestamp_start !== undefined && citation.timestamp_start !== null) {
      const time = fragment.querySelector(".citation-time");
      time.hidden = false;
      const end = citation.timestamp_end ?? citation.timestamp_start;
      time.querySelector("dd").textContent = `${formatTime(citation.timestamp_start)}-${formatTime(end)}`;
    }
    if (row.asset_id && browserImagePattern.test(citation.original_name || "")) {
      preview.href = previewUrl;
      image.src = previewUrl;
      image.alt = `${citation.title || citation.original_name} 图片预览`;
      image.addEventListener("error", () => {
        preview.remove();
        item.classList.add("no-preview");
      }, { once: true });
    } else {
      preview.remove();
      item.classList.add("no-preview");
    }
    resultsList.append(fragment);
  });
  const imageMode = items[0]?.retrieval_kind === "image_semantic";
  resultCount.textContent = imageMode ? `${items.length} 张推荐图片` : `${items.length} 条相关片段`;
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
    setNotice(errorMessage(error), "error");
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
  setNotice("正在理解问题并检索已处理资料...", "loading");
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
    const detail = data.mode === "image_semantic"
      ? "已按画面内容和图片质量排序，配文为待确认草稿。"
      : "已完成文字证据检索。";
    setNotice(`已在 ${data.dealer.official_name} 范围内完成检索。${detail}`);
  } catch (error) {
    setNotice(errorMessage(error), "error");
  } finally {
    setSearching(false);
  }
});

loadDealers();
