window.__ModuleLoader__.load({
	id: "dsh-sim-bridge",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		let React = require("react");

		const inject = ["slots"];

		const RPC = "/sim-bridge/rpc";

		function rpc(cmd, args) {
			return fetch(RPC, {
				method: "POST",
				headers: { "content-type": "application/json" },
				body: JSON.stringify(Object.assign({ cmd }, args || {}))
			}).then(function (r) { return r.json(); });
		}

		function Panel() {
			const h = React.createElement;
			const [snap, setSnap] = React.useState(null);
			const [busy, setBusy] = React.useState(false);
			const [scenes, setScenes] = React.useState([]);
			const [sceneSel, setSceneSel] = React.useState("tavern");
			const [stepN, setStepN] = React.useState("1");
			const [eventText, setEventText] = React.useState("");
			const [pathText, setPathText] = React.useState("saves/bridge_gui.json");
			const [agentSel, setAgentSel] = React.useState("");
			const [actionType, setActionType] = React.useState("speak");
			const [targetSel, setTargetSel] = React.useState("");
			const [actContent, setActContent] = React.useState("");
			const [logLines, setLogLines] = React.useState([]);
			const [expanded, setExpanded] = React.useState(() => {
				try { return localStorage.getItem("dsh-sim-bridge-panel-expanded") === "1"; } catch (e) { return false; }
			});
			const [panelEnabled, setPanelEnabled] = React.useState(true);

			const refresh = React.useCallback(async () => {
				try {
					const r = await rpc("state", {});
					if (r && r.ok) setSnap(r.data);
					else if (r) setSnap({ running: false, bridge: false, error: r.error });
				} catch (e) {
					setSnap({ running: false, bridge: false, error: String(e) });
				}
			}, []);

			// host 配置轮询：始终运行（config 命令不 spawn 桥接），实时检测面板开关
			React.useEffect(() => {
				const check = () => {
					rpc("config", {}).then((r) => {
						if (r && r.ok && r.data && typeof r.data.panelEnabled === "boolean") {
							setPanelEnabled(r.data.panelEnabled);
						}
					}).catch(() => {});
				};
				check();
				const timer = setInterval(check, 3000);
				return () => clearInterval(timer);
			}, []);

			// 状态轮询：仅面板启用时运行（开关变化由上面的 config 轮询驱动）
			React.useEffect(() => {
				if (!panelEnabled) return;
				refresh();
				const timer = setInterval(() => { refresh(); }, 3000);
				return () => clearInterval(timer);
			}, [refresh, panelEnabled]);

			React.useEffect(() => {
				if (!panelEnabled) return;
				rpc("list_scenes", {}).then((r) => {
					if (r && r.ok && r.data && Array.isArray(r.data.scenes)) setScenes(r.data.scenes);
				}).catch(() => {});
			}, [panelEnabled]);

			React.useEffect(() => {
				try { localStorage.setItem("dsh-sim-bridge-panel-expanded", expanded ? "1" : "0"); } catch (e) { /* ignore */ }
			}, [expanded]);

			const appendLog = (lines) => setLogLines((prev) => [...prev, ...lines].slice(-300));

			const run = async (cmd, payload) => {
				setBusy(true);
				try {
					const r = await rpc(cmd, payload || {});
					if (r && r.ok) {
						if (cmd === "step" && r.data && r.data.log) {
							const lines = [];
							for (const t of r.data.log) {
								lines.push("— tick " + t.tick + " —");
								for (const a of t.actions) {
									lines.push("  " + a.agent + ": " + a.action_type + (a.target ? " → " + a.target : "") + (a.content ? " 「" + a.content + "」" : ""));
									for (const m of a.messages) {
										lines.push("    [" + m.msg_type + "] " + m.sender + " → " + (m.target || "全场") + ": " + m.content);
									}
								}
							}
							appendLog(lines);
						} else if (cmd === "inject_event") {
							appendLog(["[GM] 事件注入: " + ((payload && payload.content) || "")]);
						} else if (cmd === "act_as") {
							appendLog(["[手动] 指定 " + payload.agent + " 执行 " + payload.action_type + "（下一 tick 生效）"]);
						} else if (cmd === "start" || cmd === "load") {
							setLogLines([]);
							appendLog(["[模拟] 已" + (cmd === "start" ? "启动" : "载入") + " " + ((r.data && r.data.scene) || "")]);
						} else if (cmd === "save") {
							appendLog(["[存档] 已保存到 " + ((r.data && r.data.path) || "")]);
						} else if (cmd === "quit") {
							appendLog(["[模拟] 进程已退出"]);
						}
						await refresh();
					} else {
						appendLog(["[错误] " + ((r && r.error) || "无响应")]);
					}
					return r;
				} finally {
					setBusy(false);
				}
			};

			const agents = (snap && snap.agents) || [];
			const running = !!(snap && snap.running !== false);
			const box = { border: "1px solid rgba(128,128,128,0.45)", borderRadius: 8, padding: 10, marginBottom: 4, fontFamily: "inherit", width: "100%", boxSizing: "border-box" };
			const row = { display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", marginBottom: 6 };
			const label = { fontSize: 12, color: "rgba(128,128,128,0.9)" };
			const input = { border: "1px solid rgba(128,128,128,0.5)", borderRadius: 4, padding: "2px 6px", background: "transparent", color: "inherit" };
			const btn = { borderRadius: 4, padding: "2px 8px", border: "1px solid rgba(128,128,128,0.5)", background: "rgba(128,128,128,0.12)", color: "inherit", cursor: "pointer" };
			const logBox = { maxHeight: 200, overflowY: "auto", fontFamily: "monospace", fontSize: 12, whiteSpace: "pre-wrap", borderTop: "1px solid rgba(128,128,128,0.3)", paddingTop: 6, width: "100%" };

			const statusText = running
				? "运行中 · tick " + (snap.tick !== undefined ? snap.tick : "?") + " · " + (snap.scene || "")
				: (snap && snap.error ? "桥接错误: " + snap.error : "未启动");
			const pill = { borderRadius: 10, padding: "1px 8px", fontSize: 12, border: "1px solid " + (running ? "rgba(46,160,67,0.6)" : "rgba(200,120,40,0.6)"), color: running ? "#2ea043" : "#c87828" };

			// 所有 hooks 之后才能条件返回（React 规则）
			if (!panelEnabled) return null;

			return h("div", { style: box },
				h("div", { style: row },
					h("b", null, "社会模拟"),
					h("span", { style: pill }, statusText),
					h("button", { style: btn, onClick: () => setExpanded(!expanded) }, expanded ? "收起 ▴" : "展开 ▾"),
					expanded ? h("span", { style: label }, "角色: " + (agents.map((a) => a.name).join("、") || "—")) : null,
				),
				expanded && h("div", { style: row },
					h("select", { style: input, value: sceneSel, onChange: (e) => setSceneSel(e.target.value), disabled: busy },
						(scenes.length ? scenes : ["tavern"]).map((s) => h("option", { key: s, value: s }, s))),
					h("button", { style: btn, disabled: busy || running, onClick: () => run("start", { scene: sceneSel }) }, "开始"),
					h("input", { style: Object.assign({ width: 50 }, input), value: stepN, onChange: (e) => setStepN(e.target.value) }),
					h("button", { style: btn, disabled: busy || !running, onClick: () => run("step", { ticks: parseInt(stepN, 10) || 1 }) }, "步进"),
					h("button", { style: btn, disabled: busy || !running, onClick: () => run("state", {}) }, "刷新"),
					h("button", { style: btn, disabled: busy || !running, onClick: () => run("save", { path: pathText }) }, "保存"),
					h("input", { style: Object.assign({ width: 150 }, input), value: pathText, onChange: (e) => setPathText(e.target.value) }),
					h("button", { style: btn, disabled: busy || running, onClick: () => run("load", { path: pathText }) }, "载入"),
					h("button", { style: btn, disabled: busy || !running, onClick: () => run("quit", {}) }, "退出"),
				),
				expanded && h("div", { style: row },
					h("span", { style: label }, "GM 事件:"),
					h("input", { style: Object.assign({ flex: 1, minWidth: 160 }, input), value: eventText, placeholder: "如：外面传来一声巨响", onChange: (e) => setEventText(e.target.value) }),
					h("button", { style: btn, disabled: busy || !running || !eventText, onClick: () => { run("inject_event", { content: eventText }); setEventText(""); } }, "注入"),
				),
				expanded && h("div", { style: row },
					h("span", { style: label }, "替角色行动:"),
					h("select", { style: input, value: agentSel, onChange: (e) => setAgentSel(e.target.value), disabled: busy },
						h("option", { value: "" }, "选择角色"),
						agents.map((a) => h("option", { key: a.name, value: a.name }, a.name + " (" + a.role + ")"))),
					h("input", { style: Object.assign({ width: 90 }, input), value: actionType, onChange: (e) => setActionType(e.target.value) }),
					h("input", { style: Object.assign({ width: 90 }, input), value: targetSel, placeholder: "目标", onChange: (e) => setTargetSel(e.target.value) }),
					h("input", { style: Object.assign({ flex: 1, minWidth: 120 }, input), value: actContent, placeholder: "行动内容", onChange: (e) => setActContent(e.target.value) }),
					h("button", { style: btn, disabled: busy || !running || !agentSel || !actionType, onClick: () => { run("act_as", { agent: agentSel, action_type: actionType, target: targetSel || undefined, content: actContent }); setActContent(""); } }, "执行"),
				),
				expanded && h("div", { style: row },
					h("span", { style: label }, "世界:"),
					h("span", null, (snap && snap.characters_by_location ? Object.keys(snap.characters_by_location).map((loc) => loc + "[" + snap.characters_by_location[loc].join(",") + "]").join(" | ") : "—")),
				),
				expanded && h("div", { style: logBox },
					logLines.length ? logLines.map((l, i) => h("div", { key: i }, l)) : h("span", { style: label }, "（暂无日志：点「开始」启动 tavern 场景）"),
				),
			);
		}

		function SettingsSection(props) {
			const h = React.createElement;
			const api = props && props.api;
			const [enabled, setEnabled] = React.useState(true);
			const [rev, setRev] = React.useState(null);
			const [err, setErr] = React.useState("");
			const [saving, setSaving] = React.useState(false);

			// 注意：api 网关 RPC 统一返回 { result: { ok, value | error } } 信封
			React.useEffect(() => {
				if (!api) return;
				api.settings.describe({}).then((r) => {
					const d = r && r.result && r.result.ok ? r.result.value : null;
					const ns = (d && d.namespaces || []).find((n) => n.ns === "sim-bridge");
					if (ns) {
						setEnabled(!!(ns.value && ns.value.panelEnabled));
						setRev(ns.revision);
					}
				}).catch((e) => setErr(String((e && e.message) || e)));
			}, [api]);

			const toggle = () => {
				if (!api || saving) return;
				setSaving(true);
				setErr("");
				api.settings.update({
					ns: "sim-bridge",
					patch: { panelEnabled: !enabled },
					expectedRevision: rev === null ? undefined : rev,
				}).then((r) => {
					const view = r && r.result && r.result.ok ? r.result.value : null;
					if (!view) {
						setErr((r && r.result && r.result.error && r.result.error.message) || "写入失败");
						return;
					}
					setEnabled(!!(view.value && view.value.panelEnabled));
					if (typeof view.revision === "number") setRev(view.revision);
				}).catch((e) => setErr(String((e && e.message) || e))).finally(() => setSaving(false));
			};

			const card = { border: "1px solid rgba(128,128,128,0.35)", borderRadius: 8, padding: "10px 14px" };
			const head = { fontSize: 14, fontWeight: 600, margin: "0 0 6px" };
			const row = { display: "flex", gap: 8, alignItems: "center", margin: "8px 0" };
			const desc = { fontSize: 12, color: "rgba(128,128,128,0.9)", margin: "4px 0" };
			const errStyle = { fontSize: 12, color: "#c87828", margin: "4px 0" };

			return h("div", { style: card },
				h("div", { style: head }, "社会模拟"),
				h("p", { style: desc }, "LLM 社会模拟引擎的实时控制面板（输入框上方 dock）。关闭后面板不显示、不轮询；sim_* 工具与 /sim-bridge/rpc 不受影响。修改立即生效。"),
				h("div", { style: row },
					h("input", { type: "checkbox", checked: enabled, disabled: saving, onChange: toggle }),
					h("label", null, "显示「社会模拟」面板"),
				),
				err ? h("p", { style: errStyle }, "错误: " + err) : null,
			);
		}

		function apply(ctx) {
			const connection = ctx.get("connection");
			const api = (connection && connection.api) || null;
			// 插件卡片：显示在 设置 → 插件 → 插件配置 页签
			ctx.slots.inject("settings.plugin.item", () => ctx.slots.register(
				{ name: "settings.plugin.item", id: "sim-bridge", order: 40, label: "社会模拟" },
				() => React.createElement(SettingsSection, { api })
			));
			ctx.slots.inject("conversation.input.dock", () => ctx.slots.register(
				{ name: "conversation.input.dock", id: "sim-bridge-panel", order: 30 },
				() => React.createElement(Panel, null)
			));
		}

		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	}
});
