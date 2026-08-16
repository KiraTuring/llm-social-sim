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

			const refresh = React.useCallback(async () => {
				try {
					const r = await rpc("state", {});
					if (r && r.ok) setSnap(r.data);
					else if (r) setSnap({ running: false, bridge: false, error: r.error });
				} catch (e) {
					setSnap({ running: false, bridge: false, error: String(e) });
				}
			}, []);

			React.useEffect(() => {
				refresh();
				const timer = setInterval(() => { refresh(); }, 3000);
				return () => clearInterval(timer);
			}, [refresh]);

			React.useEffect(() => {
				rpc("list_scenes", {}).then((r) => {
					if (r && r.ok && r.data && Array.isArray(r.data.scenes)) setScenes(r.data.scenes);
				}).catch(() => {});
			}, []);

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

			return h("div", { style: box },
				h("div", { style: row },
					h("b", null, "社会模拟"),
					h("span", { style: pill }, statusText),
					h("span", { style: label }, "角色: " + (agents.map((a) => a.name).join("、") || "—")),
				),
				h("div", { style: row },
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
				h("div", { style: row },
					h("span", { style: label }, "GM 事件:"),
					h("input", { style: Object.assign({ flex: 1, minWidth: 160 }, input), value: eventText, placeholder: "如：外面传来一声巨响", onChange: (e) => setEventText(e.target.value) }),
					h("button", { style: btn, disabled: busy || !running || !eventText, onClick: () => { run("inject_event", { content: eventText }); setEventText(""); } }, "注入"),
				),
				h("div", { style: row },
					h("span", { style: label }, "替角色行动:"),
					h("select", { style: input, value: agentSel, onChange: (e) => setAgentSel(e.target.value), disabled: busy },
						h("option", { value: "" }, "选择角色"),
						agents.map((a) => h("option", { key: a.name, value: a.name }, a.name + " (" + a.role + ")"))),
					h("input", { style: Object.assign({ width: 90 }, input), value: actionType, onChange: (e) => setActionType(e.target.value) }),
					h("input", { style: Object.assign({ width: 90 }, input), value: targetSel, placeholder: "目标", onChange: (e) => setTargetSel(e.target.value) }),
					h("input", { style: Object.assign({ flex: 1, minWidth: 120 }, input), value: actContent, placeholder: "行动内容", onChange: (e) => setActContent(e.target.value) }),
					h("button", { style: btn, disabled: busy || !running || !agentSel || !actionType, onClick: () => { run("act_as", { agent: agentSel, action_type: actionType, target: targetSel || undefined, content: actContent }); setActContent(""); } }, "执行"),
				),
				h("div", { style: row },
					h("span", { style: label }, "世界:"),
					h("span", null, (snap && snap.characters_by_location ? Object.keys(snap.characters_by_location).map((loc) => loc + "[" + snap.characters_by_location[loc].join(",") + "]").join(" | ") : "—")),
				),
				h("div", { style: logBox },
					logLines.length ? logLines.map((l, i) => h("div", { key: i }, l)) : h("span", { style: label }, "（暂无日志：点「开始」启动 tavern 场景）"),
				),
			);
		}

		function apply(ctx) {
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
