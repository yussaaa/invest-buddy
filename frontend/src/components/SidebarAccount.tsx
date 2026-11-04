/**
 * The foot of the sidebar: who you are on the left, Settings one click away
 * on the right.
 *
 * Settings is not a feature, so it sits here rather than in the nav — but as
 * an always-visible gear, not behind a menu. The avatar is plain for now;
 * once there is a login it becomes the way into profile and sign-out, and
 * the signed-in user replaces GUEST. Nothing here pretends sign-in exists.
 *
 * Collapsed, the rail has room for one icon per line, so the gear stacks
 * above the avatar.
 */

import { NavLink } from 'react-router-dom'
import { Settings, UserRound } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface Account {
  name: string
  /** Second line under the name; an email once accounts exist. */
  detail?: string
  avatarUrl?: string
}

const GUEST: Account = { name: 'Guest', detail: 'Local profile' }

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map(part => part[0]!.toUpperCase())
    .join('')
}

function Avatar({ account, signedIn }: { account: Account; signedIn: boolean }) {
  return (
    <span
      title={account.name}
      className="grid h-8 w-8 shrink-0 place-items-center overflow-hidden rounded-full border border-border bg-muted text-[11px] font-semibold text-foreground/80"
    >
      {account.avatarUrl ? (
        <img src={account.avatarUrl} alt="" className="h-full w-full object-cover" />
      ) : signedIn ? (
        initials(account.name)
      ) : (
        <UserRound size={15} className="text-muted-foreground" />
      )}
    </span>
  )
}

function SettingsButton() {
  return (
    <NavLink
      to="/settings"
      title="Settings"
      aria-label="Settings"
      className={({ isActive }) =>
        cn(
          'grid h-8 w-8 shrink-0 place-items-center rounded-lg transition-colors',
          isActive ? 'bg-primary/20 text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground',
        )
      }
    >
      <Settings size={16} />
    </NavLink>
  )
}

export default function SidebarAccount({
  collapsed,
  account,
}: {
  collapsed: boolean
  /** The signed-in user; omitted until login exists. */
  account?: Account
}) {
  const user = account ?? GUEST

  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-2 px-2 py-3">
        <SettingsButton />
        <Avatar account={user} signedIn={Boolean(account)} />
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2.5 px-4 py-3">
      <Avatar account={user} signedIn={Boolean(account)} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-foreground">{user.name}</p>
        {user.detail && <p className="truncate text-[11px] text-muted-foreground/70">{user.detail}</p>}
      </div>
      <SettingsButton />
    </div>
  )
}
