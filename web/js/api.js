(function () {
  "use strict";
  function handle(res) {
    return res.json().then(function (j) {
      if (!j || typeof j.code === "undefined") {
        throw new Error("响应格式异常");
      }
      if (j.code !== 0) {
        throw new Error(j.msg || ("业务错误 " + j.code));
      }
      return j.data;
    });
  }
  function get(url) {
    return fetch(url).then(handle);
  }
  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(handle);
  }
  window.API = {
    getGraph: function (band) {
      return get("/api/graph" + (band ? "?band=" + encodeURIComponent(band) : ""));
    },
    getProgress: function () { return get("/api/progress"); },
    setStatus: function (topicKey, status) {
      return post("/api/progress", { topic_key: topicKey, status: status });
    },
    snapshot: function (label) {
      return post("/api/progress/snapshot", { label: label });
    },
    listSnapshots: function () { return get("/api/progress/snapshots"); },
    restore: function (id) {
      return post("/api/progress/restore", { snapshot_id: id });
    },
    explain: function (topicKey, refresh) {
      return post("/api/ai/explain",
        { topic_key: topicKey, refresh: !!refresh });
    },
    translate: function (topicKey) {
      return post("/api/ai/translate", { topic_key: topicKey });
    },
    mistake: function (topicKey, question, myAnswer) {
      return post("/api/ai/mistake",
        { topic_key: topicKey, question: question, my_answer: myAnswer });
    },
    ask: function (topicKey, question, history) {
      return post("/api/ai/ask",
        { topic_key: topicKey, question: question, history: history });
    },
    recommend: function (k, band) {
      var q = "?k=" + (k || 30) +
        (band ? "&band=" + encodeURIComponent(band) : "");
      return get("/api/graph/recommend" + q);
    },
    practice: function (topicKey) {
      return post("/api/ai/practice", { topic_key: topicKey });
    },
    grade: function (topicKey, question, studentAnswer) {
      return post("/api/ai/grade",
        { topic_key: topicKey, question: question,
          student_answer: studentAnswer });
    }
  };
})();
