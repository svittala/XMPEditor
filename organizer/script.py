import os
import shutil

def delete_empty_directories(directory):
    for root, dirs, files in os.walk(directory, topdown=False):
        if not files:
            os.rmdir(root)

def main():
    directory = input("Enter the directory path: ")
    delete_empty_directories(directory)

if __name__ == "__main__":
    main()