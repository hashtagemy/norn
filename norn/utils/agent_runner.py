#!/usr/bin/env python3
"""
Norn Agent Runner
Wraps and executes agents with Norn monitoring
"""

import sys
import json
import importlib
import importlib.util
from pathlib import Path
from typing import Dict, Any, Optional
import traceback

from norn import NornHook
from norn.models.schemas import TaskDefinition


class AgentRunner:
    """
    Wraps and executes agents with Norn monitoring
    """
    
    def __init__(self, agent_info: Dict[str, Any]):
        self.agent_info = agent_info
        self.agent_path = self._get_agent_path()
        self.main_file = self.agent_path / agent_info['main_file']
        
    def _get_agent_path(self) -> Path:
        """Get agent directory path"""
        if self.agent_info['source'] == 'git':
            return Path(self.agent_info['clone_path'])
        else:
            return Path(self.agent_info['extract_path'])
    
    def _create_norn_hook(self) -> NornHook:
        """Create Norn monitoring hook"""
        task = TaskDefinition(
            description=self.agent_info.get('task_description', 'Agent execution'),
            max_steps=50,
            success_criteria="Complete task successfully"
        )
        
        return NornHook(
            task=task,
            mode="monitor",
            enable_ai_eval=True,
            enable_shadow_browser=False
        )
    
    def _load_agent_module(self):
        """Load agent module dynamically - handles both single files and packages"""
        if not self.main_file.exists():
            raise FileNotFoundError(f"Agent file not found: {self.main_file}")

        # Detect package structure
        from norn.api import _detect_package_info
        package_root, module_name, is_package = _detect_package_info(
            str(self.agent_path), self.agent_info['main_file'],
            self.agent_info.get('repo_root')
        )

        # BUG-003: Track paths added so run() can clean them up afterward.
        self._added_sys_paths: list[str] = []

        def _add(p: str) -> None:
            if p not in sys.path:
                sys.path.insert(0, p)
                self._added_sys_paths.append(p)

        if is_package:
            _add(package_root)
            return importlib.import_module(module_name)
        else:
            _add(str(self.agent_path))
            spec = importlib.util.spec_from_file_location("agent_module", self.main_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    
    def _find_agent_instance(self, module):
        """
        Find Agent instance in module
        Looks for:
        1. Variable named 'agent'
        2. Function that returns Agent
        3. Class that creates Agent
        """
        from strands import Agent
        
        # Check for direct agent variable
        if hasattr(module, 'agent'):
            obj = getattr(module, 'agent')
            if isinstance(obj, Agent):
                return obj
        
        # Check for main function
        if hasattr(module, 'main'):
            return 'main_function'
        
        # Check for run function
        if hasattr(module, 'run'):
            return 'run_function'
        
        # Check for Agent class instances
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, Agent):
                return obj
        
        return None
    
    def run(self, user_prompt: Optional[str] = None) -> Dict[str, Any]:
        """
        Run agent with Norn monitoring
        
        Returns:
            Dict with execution results and monitoring report
        """
        print("="*60)
        print(f"🛡️  Norn Agent Runner")
        print("="*60)
        print(f"Agent: {self.agent_info['name']}")
        print(f"Task: {self.agent_info.get('task_description', 'N/A')}")
        print(f"File: {self.main_file}")
        print("="*60)
        
        try:
            # Create Norn hook
            guard = self._create_norn_hook()
            print("✅ Norn monitoring enabled")
            
            # Load agent module
            print(f"📦 Loading agent module...")
            module = self._load_agent_module()
            print("✅ Module loaded")
            
            # Find agent
            print("🔍 Finding agent instance...")
            agent_or_func = self._find_agent_instance(module)
            
            if agent_or_func is None:
                raise ValueError("No Agent instance or main/run function found in module")
            
            # Execute based on what we found
            result = None
            
            if isinstance(agent_or_func, str):
                # It's a function name
                print(f"✅ Found function: {agent_or_func}")
                
                if agent_or_func == 'main_function':
                    func = getattr(module, 'main')
                else:
                    func = getattr(module, 'run')
                
                # Try to inject Norn
                print("🚀 Executing function...")
                
                # Check if function accepts arguments
                import inspect
                sig = inspect.signature(func)
                
                if len(sig.parameters) > 0:
                    # Function accepts arguments, try to pass guard
                    try:
                        result = func(guard=guard)
                    except TypeError:
                        # Doesn't accept guard, just run it
                        result = func()
                else:
                    result = func()
                
            else:
                # It's an Agent instance
                print("✅ Found Agent instance")
                
                # Add Norn hook
                if not hasattr(agent_or_func, '_hooks'):
                    agent_or_func._hooks = []
                
                agent_or_func._hooks.append(guard)
                print("✅ Norn hook added to agent")
                
                # Execute agent
                prompt = user_prompt or self.agent_info.get('task_description', 'Execute task')
                print(f"🚀 Executing agent with prompt: {prompt}")
                
                result = agent_or_func(prompt)
            
            print("="*60)
            print("✅ Execution completed successfully")
            print("="*60)
            
            # Get monitoring report
            report = guard.session_report
            
            if report:
                print(f"\n📊 Norn Report:")
                print(f"   Quality: {report.overall_quality}")
                print(f"   Efficiency: {report.efficiency_score}%")
                print(f"   Security: {report.security_score}%")
                print(f"   Steps: {report.total_steps}")
                print(f"   Issues: {len(report.issues)}")
                
                if report.issues:
                    print(f"\n⚠️  Issues Detected:")
                    for issue in report.issues:
                        print(f"   - [{issue.issue_type}] {issue.description}")
            
            return {
                'success': True,
                'result': str(result) if result else 'Completed',
                'report': report.model_dump() if report else None,
                'session_id': report.session_id if report else None
            }
            
        except Exception as e:
            print("="*60)
            print(f"❌ Execution failed: {str(e)}")
            print("="*60)
            traceback.print_exc()

            return {
                'success': False,
                'error': str(e),
                'traceback': traceback.format_exc()
            }
        finally:
            # BUG-003: Remove sys.path entries we added for this agent
            for _p in getattr(self, '_added_sys_paths', []):
                try:
                    sys.path.remove(_p)
                except ValueError:
                    pass


def main():
    """CLI entry point"""
    if len(sys.argv) < 2:
        print("Usage: python agent_runner.py <agent_info_json>")
        sys.exit(1)
    
    # Load agent info from JSON argument
    agent_info = json.loads(sys.argv[1])
    
    # Get optional prompt
    prompt = sys.argv[2] if len(sys.argv) > 2 else None
    
    # Run agent
    runner = AgentRunner(agent_info)
    result = runner.run(prompt)
    
    # Print result as JSON
    print("\n" + "="*60)
    print("RESULT_JSON_START")
    print(json.dumps(result, indent=2, default=str))
    print("RESULT_JSON_END")
    
    sys.exit(0 if result['success'] else 1)


if __name__ == "__main__":
    main()
