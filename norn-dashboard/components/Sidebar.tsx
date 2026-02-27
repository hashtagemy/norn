import React from 'react';
import { LayoutDashboard, Settings, FileText, Plus, Globe, Network } from 'lucide-react';

interface SidebarProps {
  currentView: string;
  onChangeView: (view: string) => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ currentView, onChangeView }) => {
  const navItems = [
    { id: 'dashboard', label: 'Overview', icon: LayoutDashboard },
    { id: 'swarms', label: 'Swarm Monitor', icon: Network },
    { id: 'browser_audit', label: 'Browser Audit', icon: Globe },
    { id: 'logs', label: 'Audit Logs', icon: FileText },
    { id: 'settings', label: 'Configuration', icon: Settings },
  ];

  return (
    <div className="w-64 border-r border-dark-border bg-dark-bg flex flex-col">
      {/* Brand */}
      <div className="h-16 flex items-center px-6 border-b border-dark-border">
        <img src="/logo.svg" alt="Norn" className="h-7" />
      </div>

      {/* Primary Action */}
      <div className="p-4 pb-2">
        <button 
          onClick={() => onChangeView('add_agent')}
          className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-phantom-600 hover:bg-phantom-500 text-white font-medium rounded-lg transition-colors shadow-lg shadow-phantom-900/20"
        >
          <Plus size={18} />
          <span>Add Agent</span>
        </button>
      </div>

      {/* Nav */}
      <div className="p-4 space-y-1">
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2 px-3">Menu</div>
        {navItems.map((item) => {
          const isActive = currentView === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onChangeView(item.id)}
              className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-200
                ${isActive 
                  ? 'bg-phantom-950/40 text-phantom-300 border border-phantom-900/50 shadow-sm' 
                  : 'text-gray-400 hover:text-gray-100 hover:bg-dark-surface'
                }`}
            >
              <item.icon size={18} className={isActive ? 'text-phantom-400' : 'text-gray-500'} />
              {item.label}
            </button>
          );
        })}
      </div>
    </div>
  );
};
