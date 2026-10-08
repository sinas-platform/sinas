/*
 * The `sinas` client every component page gets (inlined by the render
 * endpoint; no npm, no CDN, so it works air-gapped). Calls go to the
 * component's own proxy routes and agent chats, with the page's component
 * token: the viewer's permissions, capped to what the component declares.
 *
 *   sinas.input                         the inputs the page was opened with
 *   await sinas.query("ns/name", {...}) a query's result: {data, row_count, ...}
 *   await sinas.run("ns/name", {...})   a function's result
 *   const notes = sinas.store("ns/name")
 *   await notes.get(key) / set(key, value) / delete(key) / list()
 *   const bot = sinas.agent("ns/name"); await bot.send("Hi")  -> the reply text
 */
(function () {
  "use strict";
  var config = window.__SINAS_CONFIG__ || {};
  var component = config.component || {};
  var enc = encodeURIComponent;
  var base = "/components/" + enc(component.namespace) + "/" + enc(component.name);

  function ref(value, what) {
    var at = typeof value === "string" ? value.indexOf("/") : -1;
    if (at < 1 || at === value.length - 1) {
      throw new Error(what + ' must be "namespace/name", got ' + JSON.stringify(value));
    }
    return enc(value.slice(0, at)) + "/" + enc(value.slice(at + 1));
  }

  function call(path, body) {
    var headers = { "Content-Type": "application/json" };
    if (window.__SINAS_AUTH_TOKEN__) headers.Authorization = "Bearer " + window.__SINAS_AUTH_TOKEN__;
    return fetch(path, { method: "POST", headers: headers, body: JSON.stringify(body || {}) })
      .then(function (response) {
        return response.text().then(function (text) {
          var data = null;
          try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
          if (!response.ok) {
            var detail = data && data.detail ? data.detail : response.statusText;
            var error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
            error.status = response.status;
            throw error;
          }
          return data;
        });
      });
  }

  var sinas = {
    input: config.input || {},
    component: { namespace: component.namespace, name: component.name },

    query: function (name, input) {
      return call(base + "/proxy/queries/" + ref(name, "query") + "/execute", { input: input || {} });
    },

    run: function (name, input, options) {
      var body = { input: input || {} };
      if (options && options.timeout) body.timeout = options.timeout;
      return call(base + "/proxy/functions/" + ref(name, "function") + "/execute", body)
        .then(function (result) {
          if (result.status !== "success") throw new Error(result.error || "Function " + result.status);
          return result.result;
        });
    },

    store: function (name) {
      var path = base + "/proxy/states/" + ref(name, "store");
      return {
        get: function (key) {
          return call(path, { action: "get", key: key }).then(function (r) { return r.found ? r.value : null; });
        },
        set: function (key, value, options) {
          var body = { action: "set", key: key, value: value };
          if (options && options.visibility) body.visibility = options.visibility;
          return call(path, body).then(function () { return value; });
        },
        delete: function (key) {
          return call(path, { action: "delete", key: key }).then(function () {});
        },
        list: function () {
          return call(path, { action: "list" }).then(function (r) { return r.items; });
        },
      };
    },

    agent: function (name) {
      var path = ref(name, "agent");
      // One conversation per agent handle: overlapping first sends share the
      // pending chat instead of each creating one. A failed create is retried.
      var chat = null;
      function ensureChat() {
        if (!chat) {
          chat = call("/agents/" + path + "/chats", {})
            .then(function (created) { return created.id; })
            .catch(function (error) { chat = null; throw error; });
        }
        return chat;
      }
      return {
        send: function (content) {
          return ensureChat().then(function (id) {
            return call("/chats/" + enc(id) + "/messages", { content: content });
          }).then(function (message) { return message.content; });
        },
      };
    },
  };
  window.sinas = sinas;

  // Keep the token alive: renew 5 minutes before expiry, retry every 30s on
  // trouble, and say so once the session is over.
  (function keepAlive() {
    if (!window.__SINAS_AUTH_TOKEN__) return;
    var ttl = (config.tokenTtlSeconds || 3600) * 1000, margin = 300000, pause = 30000;
    var expiresAt = Date.now() + ttl;
    function ended() {
      if (document.getElementById("sinas-session-ended")) return;
      var note = document.createElement("div");
      note.id = "sinas-session-ended";
      note.textContent = "This session has ended. Reload the page to continue.";
      note.setAttribute("style", "position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:8px 12px;"
        + "background:#7f1d1d;color:#fff;font:13px system-ui,sans-serif;text-align:center");
      (document.body || document.documentElement).appendChild(note);
    }
    function retry() {
      if (Date.now() + pause < expiresAt) setTimeout(renew, pause);
      else ended();
    }
    function renew() {
      fetch(base + "/access-token", {
        method: "POST",
        headers: { Authorization: "Bearer " + window.__SINAS_AUTH_TOKEN__ },
      }).then(function (r) {
        if (r.status === 401 || r.status === 403) return ended();
        if (!r.ok) return retry();
        return r.json().then(function (body) {
          window.__SINAS_AUTH_TOKEN__ = body.token;
          expiresAt = Date.now() + body.expires_in * 1000;
          setTimeout(renew, Math.max(expiresAt - Date.now() - margin, 0));
        });
      }).catch(retry);
    }
    setTimeout(renew, ttl - margin);
  })();

  // The embedding page's light/dark switches (only "light" or "dark", only
  // from the parent) apply in place.
  window.addEventListener("message", function (event) {
    var data = event.data;
    if (event.source !== window.parent || !data || data.type !== "sinas:theme") return;
    if (data.theme === "light" || data.theme === "dark") {
      document.documentElement.style.colorScheme = data.theme;
    }
  });
})();
