import sys

def main():
    with open('src/ccgram/tmux_manager.py', 'r') as f:
        lines = f.readlines()
    
    new_lines = []
    skip = False
    for line in lines:
        if line.strip().startswith('async def discover_external_sessions(self)'):
            skip = True
        elif line.strip().startswith('async def _scan_session_windows(self'):
            skip = True
        elif line.strip().startswith('async def discover_emdash_sessions(self'):
            skip = True
        elif skip and line.strip().startswith('async def rename_window(self'):
            skip = False
            
        if not skip:
            new_lines.append(line)
            
    with open('src/ccgram/tmux_manager.py', 'w') as f:
        f.writelines(new_lines)
        
if __name__ == '__main__':
    main()
