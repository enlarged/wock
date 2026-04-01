from datetime import datetime
from colorama import Fore, Style, init

init(autoreset=True)

def logger(level: str, message: str):
    time = datetime.now().strftime("%H:%M:%S")
    
    levels = {
        "info": f"{Fore.CYAN}INFO{Style.RESET_ALL}",
        "warn": f"{Fore.YELLOW}WARN{Style.RESET_ALL}",
        "error": f"{Fore.RED}ERROR{Style.RESET_ALL}",
        "success": f"{Fore.GREEN}SUCCESS{Style.RESET_ALL}",
        "debug": f"{Fore.MAGENTA}DEBUG{Style.RESET_ALL}"
    }

    display_level = levels.get(level.lower(), f"{Fore.WHITE}{level.upper()}")
    time_stamp = f"{Fore.BLUE}{Style.BRIGHT}[{time}]{Style.RESET_ALL}"
    print(f"{time_stamp} {display_level} {message}")