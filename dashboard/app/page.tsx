const stats = [
  { label: "Servers", value: "12", note: "+2 this month" },
  { label: "Members managed", value: "24.8k", note: "+8.4% this month" },
  { label: "Automod actions", value: "1,284", note: "-12.2% this week" }
];

const modules = [
  { name: "Security", description: "Anti-raid, anti-spam, and automod controls.", status: "Configured" },
  { name: "Leveling", description: "XP rewards, rank cards, and announcements.", status: "Configure" },
  { name: "Audio", description: "Lavalink nodes, music, and voice settings.", status: "Configure" },
  { name: "Tickets", description: "Support panels and transcript logging.", status: "Configure" }
];

export default function HomePage() {
  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">T</span><span>Titanium White</span></div>
        <div className="workspace"><span className="server-icon">A</span><div><strong>Anx6ty&apos;s server</strong><small>Community workspace</small></div><span className="chevron">⌄</span></div>
        <nav aria-label="Primary navigation">
          <p className="nav-label">Workspace</p>
          <a className="nav-item active" href="#overview">Overview</a>
          <a className="nav-item" href="#modules">Modules</a>
          <a className="nav-item" href="#settings">Server settings</a>
          <a className="nav-item" href="#audit">Audit log</a>
          <p className="nav-label">Account</p>
          <a className="nav-item" href="#profile">Profile</a>
        </nav>
        <div className="sidebar-footer"><div className="avatar">A</div><div><strong>anx6ty</strong><small>Administrator</small></div><button aria-label="Account menu">•••</button></div>
      </aside>

      <section className="content">
        <header className="topbar"><div><p className="eyebrow">Saturday, September 19, 2026</p><h1>Good evening, anx6ty.</h1></div><button className="primary-button">Invite bot <span>↗</span></button></header>
        <div className="server-banner"><div className="server-icon large">A</div><div><p className="eyebrow">Selected server</p><h2>Anx6ty&apos;s server</h2></div><span className="online-dot" /> <span className="online-label">Bot online</span></div>
        <section id="overview" className="stats-grid">{stats.map((stat) => <article className="stat-card" key={stat.label}><p>{stat.label}</p><strong>{stat.value}</strong><small>{stat.note}</small></article>)}</section>
        <section id="modules" className="section"><div className="section-heading"><div><p className="eyebrow">Control center</p><h2>Bot modules</h2></div><a href="#all-modules">View all <span>→</span></a></div><div className="module-grid">{modules.map((module) => <article className="module-card" key={module.name}><div className="module-icon">✦</div><div className="module-body"><div className="module-title"><h3>{module.name}</h3><span className={module.status === "Configured" ? "status configured" : "status"}>{module.status}</span></div><p>{module.description}</p><button>{module.status === "Configured" ? "Manage module" : "Set up module"} <span>→</span></button></div></article>)}</div></section>
        <section id="audit" className="section lower-grid"><article className="activity-card"><div className="section-heading"><div><p className="eyebrow">Latest events</p><h2>Recent activity</h2></div><a href="#audit-log">Audit log <span>→</span></a></div><div className="activity-row"><span className="activity-dot warning" /><div><strong>Automod blocked a link</strong><small>2 minutes ago · #general</small></div><span className="activity-kind">Security</span></div><div className="activity-row"><span className="activity-dot success" /><div><strong>Welcome message sent</strong><small>18 minutes ago · @new-member</small></div><span className="activity-kind">Welcome</span></div><div className="activity-row"><span className="activity-dot neutral" /><div><strong>Configuration updated</strong><small>1 hour ago · by anx6ty</small></div><span className="activity-kind">Settings</span></div></article><article className="health-card"><p className="eyebrow">System status</p><h2>Everything is healthy</h2><p className="health-copy">Your bot and dashboard services are operating normally.</p><div className="health-line"><span><i className="health-dot" />Discord gateway</span><strong>Operational</strong></div><div className="health-line"><span><i className="health-dot" />Database</span><strong>Operational</strong></div><div className="health-line"><span><i className="health-dot" />Lavalink</span><strong>Operational</strong></div></article></section>
      </section>
    </main>
  );
}
