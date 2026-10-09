import { Code, Plug, Zap } from 'lucide-react'

// The owner's drawing: source → bronze → silver → gold, out to three channels. Static decoration.
const SCALE = 400 / 500
const ICON = 13

const LINES = [
  { d: 'M72 118H98', stroke: '#c1bfff' },
  { d: 'M138 118H166', stroke: '#c1bfff' },
  { d: 'M206 118H232', stroke: '#56e2e1' },
  { d: 'M272 118C298 118 296 76 318 76', stroke: '#7c6bd6' },
  { d: 'M272 118H318', stroke: '#c1bfff' },
  { d: 'M272 118C298 118 296 160 318 160', stroke: '#56e2e1' },
]

const DOTS = [
  { cx: 76, cy: 118, fill: '#512eab' },
  { cx: 96, cy: 118, fill: '#512eab' },
  { cx: 142, cy: 118, fill: '#512eab' },
  { cx: 162, cy: 118, fill: '#512eab' },
  { cx: 212, cy: 118, fill: '#56e2e1' },
  { cx: 228, cy: 118, fill: '#56e2e1' },
  { cx: 278, cy: 118, fill: '#7c6bd6' },
  { cx: 313, cy: 76, fill: '#7c6bd6' },
  { cx: 313, cy: 118, fill: '#c1bfff' },
  { cx: 313, cy: 160, fill: '#56e2e1' },
]

const LAYERS = [
  { cx: 118, fill: '#b5652a', letter: 'B' },
  { cx: 186, fill: '#8b8f98', letter: 'S' },
]

// Box centre y in the viewBox; the box is 30 wide at x 322.
const CHANNELS = [
  { cy: 76, name: 'Events', Icon: Zap },
  { cy: 118, name: 'API / MCP', Icon: Code },
  { cy: 160, name: 'Integrations', Icon: Plug },
]

export function PipelineDrawing() {
  return (
    <div
      className="relative hidden h-[188px] w-[400px] shrink-0 lg:block"
      role="img"
      aria-label="Source tables flow into bronze, silver and gold, then out as events, API and MCP, and integrations"
    >
      <svg width="400" height="188" viewBox="0 0 500 235" aria-hidden="true">
        <g fill="none" stroke="#ffffff" strokeOpacity=".10" strokeWidth="2">
          {[48, 92, 138, 188].map((r) => (
            <circle key={r} cx="252" cy="118" r={r} />
          ))}
        </g>
        <g fill="none" stroke="#56e2e1" strokeOpacity=".22" strokeWidth="1.5">
          {[70, 162].map((r) => (
            <circle key={r} cx="252" cy="118" r={r} />
          ))}
        </g>
        <g fill="none" stroke="#ffffff" strokeOpacity=".14" strokeWidth="6" strokeLinecap="round">
          {LINES.map((l) => (
            <path key={l.d} d={l.d} />
          ))}
        </g>
        <g fill="none" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="7 9">
          {LINES.map((l) => (
            <path key={l.d} d={l.d} stroke={l.stroke} />
          ))}
        </g>
        <g>
          {DOTS.map((d) => (
            <circle key={`${d.cx}-${d.cy}`} cx={d.cx} cy={d.cy} r="3" fill={d.fill} />
          ))}
        </g>
        <circle cx="52" cy="118" r="20" fill="#ffffff" stroke="#512eab" strokeWidth="2.5" />
        <g fill="none" stroke="#0d004d" strokeWidth="2.2" strokeLinecap="round">
          <ellipse cx="52" cy="110" rx="8.5" ry="3.5" />
          <path d="M43.5 110v16c0 2 3.8 3.5 8.5 3.5s8.5-1.5 8.5-3.5v-16" />
          <path d="M43.5 118c0 2 3.8 3.5 8.5 3.5s8.5-1.5 8.5-3.5" />
        </g>
        {LAYERS.map((l) => (
          <g key={l.letter}>
            <circle cx={l.cx} cy="118" r="20" fill={l.fill} />
            <text x={l.cx} y="124" textAnchor="middle" fontSize="16" fontWeight="800" fill="#fff">
              {l.letter}
            </text>
          </g>
        ))}
        <circle cx="252" cy="118" r="28" fill="none" stroke="#ffce00" strokeOpacity=".55" strokeWidth="2.5" />
        <circle cx="252" cy="118" r="36" fill="none" stroke="#ffce00" strokeOpacity=".25" strokeWidth="2" />
        <circle cx="252" cy="118" r="20" fill="#c9a13a" />
        <text x="252" y="124" textAnchor="middle" fontSize="16" fontWeight="800" fill="#fff">
          G
        </text>
        <g fill="#ffffff" fillOpacity=".14">
          {CHANNELS.map((c) => (
            <rect key={c.name} x="322" y={c.cy - 15} width="30" height="30" rx="8" />
          ))}
        </g>
        <g fill="#ffffff" fontSize="13" fontWeight="700">
          {CHANNELS.map((c) => (
            <text key={c.name} x="362" y={c.cy + 5}>
              {c.name}
            </text>
          ))}
        </g>
      </svg>
      {CHANNELS.map(({ cy, name, Icon }) => (
        <Icon
          key={name}
          size={ICON}
          strokeWidth={2}
          aria-hidden="true"
          className="absolute text-white"
          style={{ left: 337 * SCALE - ICON / 2, top: cy * SCALE - ICON / 2 }}
        />
      ))}
    </div>
  )
}
