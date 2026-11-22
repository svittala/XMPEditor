import os
import shutil
import logging
import argparse

# set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

VALID_EXTENSIONS = (
    '.jpg', '.jpeg', '.cr3', '.cr2', '.nef', '.arw', '.dng',
    '.heic', '.xmp', '.png', '.gif', '.tif', '.tiff'
)

def move_images_and_metadata(source_folder, destination_folder):
    if not os.path.exists(source_folder):
        logging.error(f"Source folder does not exist: {source_folder}")
        return

    if not os.path.exists(destination_folder):
        os.makedirs(destination_folder)
        logging.info(f"Created destination root folder: {destination_folder}")

    # iterate through all subfolders in the source folder
    for src_dir in os.listdir(source_folder):
        src_dir_path = os.path.join(source_folder, src_dir)
        if os.path.isdir(src_dir_path):
            # create the corresponding destination folder
            dest_dir = os.path.join(destination_folder, src_dir)
            if not os.path.exists(dest_dir):
                os.makedirs(dest_dir)

            # move files from the source folder to the destination folder
            files_moved = 0
            for file in os.listdir(src_dir_path):
                if file.lower().endswith(VALID_EXTENSIONS):
                    src_file = os.path.join(src_dir_path, file)
                    dest_file = os.path.join(dest_dir, file)
                    shutil.move(src_file, dest_file)
                    logging.info(f'Moved file: {file} -> {dest_dir}')
                    files_moved += 1

            logging.info(f'Completed moving {files_moved} file(s) from {src_dir} to {dest_dir}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Move images and metadata files from subfolders to matching destination subfolders."
    )
    parser.add_argument(
        '-s', '--source',
        required=True,
        help="Path to the source folder containing subfolders"
    )
    parser.add_argument(
        '-d', '--destination',
        required=True,
        help="Path to the destination folder where subfolders should match"
    )

    args = parser.parse_args()
    move_images_and_metadata(source_folder=args.source, destination_folder=args.destination)