import argparse
import csv
from collections import namedtuple
from glob import glob
from itertools import product
import os

from bs4 import BeautifulSoup
from pathos.pools import ProcessPool
from pathos.helpers import cpu_count
import requests
from tqdm import tqdm


SEC_GOV_URL = 'http://www.sec.gov/Archives'
FORM_INDEX_URL = os.path.join(SEC_GOV_URL,'edgar','full-index','{}','QTR{}','form.idx')

IndexRecord = namedtuple("IndexRecord",["form_type","company_name","cik","date_filed","filename"])

invalid_spaces = [u'\xa0',u'&nbsp;']

class FormIndex(object):
    def __init__(self, index_dir):
        self.formrecords = []

        self.index_dir = index_dir
        if not os.path.exists(index_dir):
            os.makedirs(index_dir)

    def retrieve(self, year, qtr):

        form_idx = "form_year{}_qtr{}.index".format(year,qtr)
        form_idx_path = os.path.join(self.index_dir,form_idx)

        self.download(form_idx_path, year, qtr)
        self.extract(form_idx_path)

    def download(self, form_idx_path, year, qtr):
        if os.path.exists(form_idx_path):
            print("Download skipped: year {}, qtr {}".format(year,qtr))
            return

        # Download and save to cached directory
        print("Downloading year {}, qtr {}".format(year,qtr))

        index_url = FORM_INDEX_URL.format(year,qtr)
        resp = requests.get(index_url)

        with open(form_idx_path,'wb') as fout:
            fout.write(resp.content)

    def extract(self, form_idx_path):
        # Parse row to record
        def parse_row_to_record(row, fields_begin):
            record = []

            for begin, end in zip(fields_begin[:],fields_begin[1:] + [len(row)] ):
                field = row[begin:end].rstrip()
                field = field.strip('\"')
                record.append(field)

            return record

        # Read and parse
        print("Extracting from {}".format(form_idx_path))

        with open(form_idx_path,'rb') as fin:
            # If arrived at 10-K section of forms
            arrived = False

            for row in fin.readlines():
                row = row.decode('ascii')
                if row.startswith("Form Type"):
                    fields_begin = [ row.find("Form Type"),
                                     row.find("Company Name"),
                                     row.find('CIK'),
                                     row.find('Date Filed'),
                                     row.find("File Name") ]

                elif row.startswith("10-K "):
                    arrived = True
                    rec = parse_row_to_record(row,fields_begin)
                    self.formrecords.append(IndexRecord(*rec))

                elif arrived == True:
                    break

    def save(self, path):
        print("Saving records to {}".format(path))

        with open(path,'w') as fout:
            writer = csv.writer(fout,delimiter=',',quotechar='\"',quoting=csv.QUOTE_ALL)
            for rec in self.formrecords:
                writer.writerow( tuple(rec) )

class Form(object):
    def __init__(self, form_dir):
        # Initialize cache directory
        self.form_dir = form_dir
        if not os.path.exists(form_dir):
            os.makedirs(form_dir)

    def download(self, form10k_savepath):

        def iter_path_generator(form10k_savepath):
            with open(form10k_savepath,'r') as fin:
                reader = csv.reader(fin,delimiter=',',quotechar='\"',quoting=csv.QUOTE_ALL)
                for row in reader:
                    form_type, company_name, cik, date_filed, filename = row
                    url = os.path.join(SEC_GOV_URL,filename)
                    yield url

        def download_job(url):
            fname = '_'.join(url.split('/')[-2:])

            fname, ext = os.path.splitext(fname)
            fname = fname + '.html'

            formpath = os.path.join(self.form_dir,fname)

            if os.path.exists(formpath):
                print("Already exists, skipping {}".format(url))
            else:
                print("Downloading {}".format(url))

                r = requests.get(url)

                with open(formpath,'wb') as fout:
                    fout.write(r.content)

        ncpus = cpu_count()
        pool = ProcessPool( ncpus )
        #pool = ProcessPool( 8 )
        pool.map( download_job, iter_path_generator(form10k_savepath) )

class MDAParser(object):
    def __init__(self, mda_dir):
        self.mda_dir    = mda_dir
        if not os.path.exists(mda_dir):
            os.makedirs(mda_dir)

        self.empty_mdas = []

    def __del__(self):
        emptymda_paths = 'failed2parse.txt'
        print("Writing failed to parse files to {}".format(emptymda_paths))

        with open(emptymda_paths,'w') as fout:
            for line in self.empty_mdas:
                fout.write(line + '\n')

    def extract_from(self, form_dir):

        for fname in os.listdir(form_dir):
            if fname.endswith('.txt'):
                pass

            filepath = os.path.join(form_dir,fname)
            print("Parsing: {}".format(filepath))

            with open(filepath,'rb') as fin:
                markup = fin.read()

            html_name, ext = os.path.splitext(filepath)

            parsed_mda = self.parse(markup, html_name)

            # Get save file path for mda
            name, ext = os.path.splitext(fname)

            mdafname = name + '.mda'

            mdapath = os.path.join(self.mda_dir,mdafname)

            if not parsed_mda:
                print("Empty mda: {}".format(mdapath))
                self.empty_mdas.append(filepath)
            else:
                with open(mdapath,'w') as fout:
                    fout.write(str(parsed_mda))

    def parse(self, markup, html_name):
        parsed_mda = ""
        try:
            soup = BeautifulSoup(markup,'html.parser')
            text = soup.get_text('\n', strip=True)

            text = text.replace(u'\xa0', u' ').replace(u'&nbsp;',u' ').upper()

            item14 = '\nITEM 14'
            item7 = '\nITEM 7.'
            item7A = '\nITEM 7A.'
            item8 = '\nITEM 8.'

            start = text.find(item14)

            begin = text.find(item7, start)
            if begin == -1:
                begin = text.find('\nI\nTEM\n7.')
                end = text.find('\nI\nTEM\n7A.')
                if end == -1:
                    end = text.find('\nI\nTEM\n8.')
            else:
                end   = text.find(item7A, begin)
                if end == -1:
                    end = text.find(item8)

            if end > begin:
                parsed_mda = text[begin:end];

            if True or not parsed_mda:
                foutname = html_name + '.txt'
                print ("Writing parsed to {}".format(foutname))
                with open(foutname,'w') as fout:
                    fout.write(text)

        except:
            print("BeautifulSoup parsing failed {}".format(html_name + '.html'))


        return parsed_mda

def main():
    # Download form index
    parser = argparse.ArgumentParser("Edgar 10k forms sentiment analysis")
    parser.add_argument('--year_start',type=int,default=2014)
    parser.add_argument('--year_end',type=int,default=2016)
    parser.add_argument('--index_dir',type=str,default='./index')
    parser.add_argument('--form_dir',type=str,default='./form')
    parser.add_argument('--mda_dir',type=str,default='./mda')
    args = parser.parse_args()

    year_start = args.year_start
    year_end = args.year_end

    index_dir = args.index_dir
    form_dir = args.form_dir
    mda_dir = args.mda_dir

    # Download and extract 10k form indices
    form10k_savepath = "year{}-{}.10k.csv".format(year_start,year_end)

    if not os.path.exists(form10k_savepath):
        formindex = FormIndex(index_dir=index_dir)
        for year, qtr in product(range(args.year_start,args.year_end+1),range(1,5)):
            formindex.retrieve(year, qtr)
        formindex.save(form10k_savepath)
    else:
        print("{} already exists".format(form10k_savepath))

    # Download 10k forms raw data
    #form = Form(form_dir=form_dir)
    #form.download(form10k_savepath=form10k_savepath)

    # Extract MD&A
    parser = MDAParser(mda_dir=mda_dir)
    parser.extract_from(form_dir=form_dir)

if __name__ == "__main__":
    main()
