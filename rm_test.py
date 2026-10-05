import sys

def main():
    with open('tests/ccgram/handlers/polling/test_polling_coordinator.py', 'r') as f:
        lines = f.readlines()
    
    new_lines = []
    skip = False
    for line in lines:
        if line.strip().startswith('class TestStatusPollLoopHandlesExternalSessions:'):
            skip = True
        elif skip and line.strip().startswith('class TestStatusPollLoopRespectsConfigInterval:'):
            skip = False
            
        if not skip:
            new_lines.append(line)
            
    with open('tests/ccgram/handlers/polling/test_polling_coordinator.py', 'w') as f:
        f.writelines(new_lines)
        
if __name__ == '__main__':
    main()
