// ===================================================
//  8维能力测评 · 报告内容引擎
//  读取 report_engine_data.js 的数据，提供动态报告内容
// ===================================================
const ReportEngine = (function() {

  const DIM_ORDER = ['COG','TEC','COM','SOC','ORG','PRS','MGT','LLA'];

  // ---------- 核心辅助函数 ----------

  // 按得分降序排列维度
  function sortDimensions(scores) {
    return DIM_ORDER
      .map(d => ({ dim: d, score: scores[d].average }))
      .sort((a, b) => b.score - a.score);
  }

  // 是否为极端单维突出：top1 >= 4.3 且其余 7 维最高 <= 3.5
  function isSingleDimExtreme(sorted) {
    return sorted[0].score >= 4.3 && sorted[1].score <= 3.5;
  }

  // 获取分数级别标签: low(<3.0), mid(3.0-3.9), high(>=4.0)
  function getScoreLevel(score) {
    if (score >= 4.0) return 'high';
    if (score >= 3.0) return 'mid';
    return 'low';
  }

  // ---------- 公开 API ----------

  return {

    // 匹配人格画像
    // 返回值: { name, metaphor, coreDrive, successDef, top1, top2 }
    matchPersona(scores) {
      const sorted = sortDimensions(scores);
      const top1 = sorted[0].dim;
      const top2 = sorted[1].dim;

      // 先尝试极端单维匹配
      if (isSingleDimExtreme(sorted)) {
        const extremeKey = top1 + '_EXTREME';
        if (PERSONA_DB[extremeKey]) {
          return {
            ...PERSONA_DB[extremeKey],
            top1, top2
          };
        }
      }

      // 精确匹配 top1_top2
      const exactKey = top1 + '_' + top2;
      if (PERSONA_DB[exactKey]) {
        return {
          ...PERSONA_DB[exactKey],
          top1, top2
        };
      }

      // Fallback 1: 用 top1 的任意条目
      const fallbackKey1 = Object.keys(PERSONA_DB).find(k => k.startsWith(top1 + '_'));
      if (fallbackKey1) {
        return {
          ...PERSONA_DB[fallbackKey1],
          top1, top2
        };
      }

      // Fallback 2: 默认画像
      return {
        ...PERSONA_FALLBACK,
        top1, top2
      };
    },

    // 获取维度张力分析
    // 参数: 高维代码, 低维代码
    // 返回值: { conflict, scenario: { title, innerOS, analysis }, landmine, prevention } 或 null
    getTension(highDim, lowDim) {
      // 尝试直接查询
      const key = highDim + '_' + lowDim;
      if (TENSION_DB[key]) {
        return TENSION_DB[key];
      }
      // 反向配对（如果只写了反方向的数据）
      const reverseKey = lowDim + '_' + highDim;
      if (TENSION_DB[reverseKey]) {
        return TENSION_DB[reverseKey];
      }
      return null;
    },

    // 获取缺陷价值化重塑
    // 参数: 维度代码, 分数
    // 返回值: { title, reframe, workplace } 或 null
    getReframe(dim, score) {
      const level = getScoreLevel(score);
      // 高分不重塑
      if (level === 'high') return null;

      if (REFRAME_DB[dim] && REFRAME_DB[dim][level]) {
        return REFRAME_DB[dim][level];
      }
      return null;
    },

    // 获取寄语
    // 参数: 8维平均分 (0-5)
    // 返回值: { message, tone }
    getClosing(totalAvg) {
      // 从高分到低分匹配
      for (const m of CLOSING_MESSAGES) {
        if (totalAvg >= m.minScore && totalAvg < m.maxScore) {
          return { message: m.message, tone: m.tone };
        }
      }
      // 兜底
      return { message: CLOSING_MESSAGES[CLOSING_MESSAGES.length - 1].message, tone: 'support' };
    },

    // 获取排序后的维度列表（用途：方便渲染时取 top3/bot3）
    getSortedDims(scores) {
      return sortDimensions(scores);
    },

    // 获取分数颜色
    getScoreColor(score) {
      if (score >= 4.0) return '#059669';
      if (score >= 3.0) return '#2563eb';
      return '#ea580c';
    },

    // 获取等级标签
    getLevelInfo(score) {
      if (score >= 4.0) return { label: '优秀', badge: 'result-badge high', color: '#059669' };
      if (score >= 3.0) return { label: '良好', badge: 'result-badge mid', color: '#2563eb' };
      return { label: '待提升', badge: 'result-badge low', color: '#ea580c' };
    }
  };

})();
