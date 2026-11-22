import os

def delete_picasa_ini(directory):
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file == 'Thumbs.db' or file == 'Picasa.ini':
                os.remove(os.path.join(root, file))
                print(f"Deleted {file}")

directory = "E:\\songs_photos"
delete_picasa_ini(directory)