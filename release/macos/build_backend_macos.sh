# navigate to the root directory

# Creating the wheel
# Creating the wheel
pip install huggingface_hub
python -c 'from huggingface_hub import get_full_repo_name; print("success")'
python setup.py sdist bdist_wheel
pip install "dist/alphadia-1.5.5-py3-none-any.whl[stable]"

# Creating the stand-alone pyinstaller folder
pip install pyinstaller
python -c 'from huggingface_hub import get_full_repo_name; print("success")'
#pyinstaller release/pyinstaller/alphadia.spec -y